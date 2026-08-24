"""Trabalho que roda FORA do processo que atende HTTP.

Por que existe: ate a fase 2 a ingestao rodava em `BackgroundTasks`, ou seja,
no proprio processo do uvicorn, depois da resposta. Um redeploy no meio de uma
ingestao longa perdia o trabalho em silencio, e uma operacao bloqueante travava
ate o `/healthz`. Tudo que mora aqui e chamado por um worker separado.
"""
