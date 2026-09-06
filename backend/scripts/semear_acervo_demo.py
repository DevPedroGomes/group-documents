"""Semeia o acervo de demonstração numa conta, pelo caminho real de ingestão.

POR QUE
-------
A demo do BrainHub não se sustenta com três PDFs: qualquer pessoa faz isso no chat
de um provedor, com uma experiência melhor. O que um provedor não faz é acervo,
com centenas de documentos indexados, versões que se contradizem e vigência no
tempo. Este script põe esse acervo de pé.

COMO
----
Espelha exatamente o que `POST /documents/upload` faz, na mesma ordem:

    save_file()  ->  INSERT em documents  ->  enfileirar("ingerir", ...)

Não chama a rota HTTP de propósito: ela exige JWT, aplica rate limit por IP e
consome a cota diária de visitante. Nada disso deveria valer para uma operação
de manutenção feita pelo dono da máquina.

⚠️ O TETO DIÁRIO É DELIBERADAMENTE PULADO
------------------------------------------
`metering.consumir("ingest", ...)` existe para impedir que UM VISITANTE torre o
orçamento do dia. Semear é o operador enchendo a própria conta de demonstração:
consumir a cota aqui bloquearia o script no documento 100 e ainda deixaria a demo
sem cota para o resto do dia, que é o oposto do que o teto existe para proteger.
O gasto real continua acontecendo e continua sendo do operador: use `--limite`
para semear em lotes e `--estimar` para ver o tamanho antes.

⚠️ O LIMITE DA CONTA VOYAGE MANDA NO RITMO
-------------------------------------------
Medido em 06/09/2026, semeando 10 documentos: **2 falharam** com `RateLimitError`.
A conta Voyage está sem forma de pagamento, e nesse estado o limite é de
**3 requisições por minuto**. Como a ingestão faz uma chamada de embedding por
documento, semear 500 levaria ~2h48 no melhor caso, e na prática o que acontece é
o job estourar a retentativa e cair na dead-letter.

O conserto não é código: é adicionar forma de pagamento no dashboard da Voyage.
Os 200M tokens gratuitos continuam valendo depois disso, então **não passa a
custar** — só destrava o limite. Enquanto isso não acontecer, use `--rpm 3`, que
espaça o enfileiramento para caber no limite.

IDEMPOTENTE
-----------
A chave é o título, que carrega o nome do arquivo do acervo. Rodar de novo pula o
que já existe, então um lote interrompido continua de onde parou sem duplicar
chunk nem pagar embedding duas vezes.

USO
    python -m scripts.semear_acervo_demo --email demo@gomio.com.br --estimar
    python -m scripts.semear_acervo_demo --email demo@gomio.com.br --limite 50
    python -m scripts.semear_acervo_demo --email demo@gomio.com.br
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import insert, select
from sqlalchemy import text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import documents, users
from app.services.file_storage import save_file
from scripts.gerar_acervo_demo import Documento, gerar

MIME_PDF = "application/pdf"
PREFIXO = "[acervo-demo]"
"""Marca o título dos documentos semeados.

Serve a duas coisas: torna a semeadura idempotente sem tabela nova, e deixa
`--limpar` apagar o acervo sem tocar em documento que a pessoa subiu à mão.
"""


def _hash_senha(senha: str) -> str:
    # Importado do módulo de auth para não existirem dois jeitos de derivar o
    # hash. Se o algoritmo mudar lá, muda aqui junto.
    from app.api.routes.auth import _hash_password

    return _hash_password(senha)


def buscar_usuario(email: str) -> str | None:
    with engine.begin() as conn:
        existente = conn.execute(select(users.c.id).where(users.c.email == email)).scalar()
    return str(existente) if existente else None


def garantir_usuario(email: str, senha: str) -> str:
    """Devolve o `user_id` da conta de demonstração, criando-a se preciso."""
    with engine.begin() as conn:
        existente = conn.execute(select(users.c.id).where(users.c.email == email)).scalar()
        if existente:
            return str(existente)

        user_id = str(uuid.uuid4())
        conn.execute(
            insert(users).values(
                id=user_id,
                email=email,
                password_hash=_hash_senha(senha),
                full_name="Aurora Coffee Roasters (demonstração)",
            )
        )
        return user_id


def ja_semeados(user_id: str) -> set[str]:
    with engine.begin() as conn:
        linhas = conn.execute(
            sqltext("SELECT title FROM documents WHERE user_id = :u AND title LIKE :p"),
            {"u": user_id, "p": f"{PREFIXO}%"},
        ).fetchall()
    return {linha[0] for linha in linhas}


def titulo_de(doc: Documento) -> str:
    return f"{PREFIXO} {doc.titulo}"


async def reprocessar(user_id: str, rpm: float = 0) -> int:
    """Reenfileira o que ficou em `pending` ou `failed`.

    Semeadura sob limite de taxa SEMPRE deixa retardatário: o job estoura a
    retentativa e cai na dead-letter, ou fica preso em `pending` porque a fila
    desistiu. Sem isto, a única saída seria limpar tudo e pagar o embedding de
    novo no acervo inteiro para recuperar dois documentos.
    """
    from agent_ops.decisions import digerir
    from agent_ops.queue import criar_pool, enfileirar

    with engine.begin() as conn:
        linhas = conn.execute(
            sqltext(
                "SELECT id, storage_path FROM documents "
                "WHERE user_id = :u AND title LIKE :p AND status IN ('pending','failed')"
            ),
            {"u": user_id, "p": f"{PREFIXO}%"},
        ).fetchall()

    if not linhas:
        return 0

    fila = await criar_pool()
    intervalo = 60.0 / rpm if rpm > 0 else 0.0
    for doc_id, storage_path in linhas:
        # Volta para `pending` antes de reenfileirar: `failed` que continua
        # `failed` na fila esconde se o reprocessamento sequer começou.
        with engine.begin() as conn:
            conn.execute(
                sqltext("UPDATE documents SET status = 'pending' WHERE id = :id"), {"id": doc_id}
            )
        digest = digerir(f"reprocesso:{doc_id}:{storage_path}")
        await enfileirar(
            fila, "ingerir", str(doc_id), user_id, storage_path,
            digest=digest, tenant=user_id,
        )
        if intervalo:
            await asyncio.sleep(intervalo)
    return len(linhas)


def limpar(user_id: str) -> int:
    """Apaga só o que este script semeou. `chunks` cai por ON DELETE CASCADE."""
    with engine.begin() as conn:
        n = conn.execute(
            sqltext("DELETE FROM documents WHERE user_id = :u AND title LIKE :p"),
            {"u": user_id, "p": f"{PREFIXO}%"},
        ).rowcount
    return int(n or 0)


async def semear(user_id: str, docs: list[Documento], destino_pdf: Path, rpm: float = 0) -> tuple[int, int]:
    """Grava, registra e enfileira. Devolve (semeados, pulados)."""
    # Import tardio e a MESMA fila que a app usa: `criar_pool` do agent-ops, do
    # jeito que `app/main.py` faz no lifespan. Uma fila diferente aqui semearia
    # numa fila que worker nenhum consome.
    from agent_ops.decisions import digerir
    from agent_ops.queue import criar_pool, enfileirar

    existentes = ja_semeados(user_id)
    fila = await criar_pool()
    semeados = pulados = 0

    # O freio fica no ENFILEIRAMENTO, e nao no worker, porque a fila e FIFO e o
    # worker puxa um job por vez: espacar a entrada espaca o consumo, sem tocar
    # no worker nem inventar um segundo lugar onde o ritmo e decidido.
    intervalo = 60.0 / rpm if rpm > 0 else 0.0

    for doc in docs:
        titulo = titulo_de(doc)
        if titulo in existentes:
            pulados += 1
            continue

        pdf = destino_pdf / Path(doc.nome_arquivo).with_suffix(".pdf").name
        if not pdf.is_file():
            print(f"  ! sem PDF para {doc.nome_arquivo}, pulando", file=sys.stderr)
            pulados += 1
            continue

        storage_path = save_file(user_id, MIME_PDF, pdf.read_bytes())
        with engine.begin() as conn:
            doc_id = conn.execute(
                insert(documents)
                .values(
                    user_id=user_id,
                    title=titulo,
                    mime=MIME_PDF,
                    storage_path=storage_path,
                    status="pending",
                    # A data de emissão vive em `meta` porque é ela que dá sentido
                    # ao recorte no tempo: sem isto, `as_of` não teria em que se
                    # apoiar para este acervo.
                    meta={"emitido_em": doc.emitido_em.isoformat(), "categoria": doc.categoria},
                )
                .returning(documents.c.id)
            ).scalar_one()

        # O digest identifica ESTA ingestao (linha + arquivo), como no upload:
        # `doc_id` e `storage_path` sao novos a cada semeadura, entao ele da um
        # `job_id` deterministico sem prometer dedup entre execucoes. A
        # idempotencia deste script vem do titulo, nao daqui.
        digest = digerir(f"{doc_id}:{storage_path}")
        await enfileirar(
            fila, "ingerir", str(doc_id), user_id, storage_path,
            digest=digest, tenant=user_id,
        )
        semeados += 1
        if semeados % 25 == 0:
            print(f"  {semeados} enfileirados...", flush=True)
        if intervalo:
            await asyncio.sleep(intervalo)

    return semeados, pulados


def main() -> None:
    p = argparse.ArgumentParser(description="Semeia o acervo de demonstração da Aurora.")
    # NAO usar TLD reservado (.local, .test, .example, .invalid): o `email-validator`
    # que valida o /auth/login recusa todos eles. A conta criada direto por SQL
    # existiria e nunca conseguiria entrar. Descoberto em 06/09/2026, semeando.
    p.add_argument("--email", default="demo@gomio.com.br")
    p.add_argument("--senha", default="acervo-de-demonstracao")
    p.add_argument("--quantidade", type=int, default=500)
    p.add_argument("--limite", type=int, default=0, help="semeia no máximo N nesta execução (0 = todos)")
    p.add_argument("--rpm", type=float, default=0,
                   help="espaça o enfileiramento para N documentos por minuto (0 = sem freio). "
                        "Use 3 enquanto a conta Voyage estiver sem forma de pagamento.")
    p.add_argument("--pdfs", type=Path, default=Path("/tmp/acervo-demo"))
    p.add_argument("--estimar", action="store_true", help="não escreve nada; só diz o tamanho do trabalho")
    p.add_argument("--limpar", action="store_true", help="apaga o acervo semeado desta conta e sai")
    p.add_argument("--reprocessar", action="store_true",
                   help="reenfileira os documentos que ficaram em pending ou failed, e sai")
    args = p.parse_args()

    docs = gerar(args.quantidade)

    # `--estimar` nao pode escrever nada, nem criar a conta: um dry-run que deixa
    # rastro no banco perde a serventia de ser um dry-run.
    if args.estimar:
        user_id = buscar_usuario(args.email)
        existentes = ja_semeados(user_id) if user_id else set()
        pendentes = [d for d in docs if titulo_de(d) not in existentes]
        if args.limite:
            pendentes = pendentes[: args.limite]
        print(f"conta:        {args.email}  ({user_id or 'ainda nao existe'})")
        print(f"no acervo:    {len(docs)}")
        print(f"ja semeados:  {len(existentes)}")
        print(f"a semear:     {len(pendentes)}")
        print(f"custo:        ~1 chunk por documento, entao ~{len(pendentes)} enriquecimentos")
        print(f"              + ~{len(pendentes)} chamadas de embedding (uma por documento)")
        return

    user_id = garantir_usuario(args.email, args.senha)

    if args.limpar:
        print(f"{limpar(user_id)} documentos removidos de {args.email}")
        return

    if args.reprocessar:
        n = asyncio.run(reprocessar(user_id, args.rpm))
        print(f"{n} documentos reenfileirados")
        return

    existentes = ja_semeados(user_id)
    pendentes = [d for d in docs if titulo_de(d) not in existentes]
    if args.limite:
        pendentes = pendentes[: args.limite]

    if not args.pdfs.is_dir():
        raise SystemExit(
            f"{args.pdfs} não existe. Gere o acervo primeiro:\n"
            f"  python -m scripts.gerar_acervo_demo --saida {args.pdfs} "
            f"--quantidade {args.quantidade} --formato pdf"
        )

    if args.rpm:
        minutos = len(pendentes) / args.rpm
        print(f"freio de {args.rpm}/min: {len(pendentes)} documentos levam ~{minutos:.0f} minutos\n", flush=True)

    semeados, pulados = asyncio.run(semear(user_id, pendentes, args.pdfs, args.rpm))
    print(f"\n{semeados} enfileirados, {pulados} pulados")
    print(f"conta: {args.email}  ({user_id})")
    print("acompanhe: docker logs -f group-documents-worker")


if __name__ == "__main__":
    main()
