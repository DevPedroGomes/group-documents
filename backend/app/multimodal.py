import os
import tempfile
import time
from agno.utils.log import logger
import google.generativeai as genai

# Configurar API Key
# IMPORTANTE: O usuário deve definir GOOGLE_API_KEY no .env
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

def process_media_with_gemini(data: bytes, mime_type: str, prompt: str = "Descreva este conteúdo em detalhes.", filename: str | None = None) -> str:
    """
    Função genérica para processar mídia (Audio, Vídeo, Imagem) usando Gemini.
    """
    temp_path = None
    try:
        # 1. Salvar bytes em arquivo temporário (Gemini File API exige arquivo para multimídia pesada)
        suffix = ".bin"
        if filename:
            ext = os.path.splitext(filename)[1]
            if ext: suffix = ext
        
        # Fallback se não tiver filename ou extensão
        if suffix == ".bin":
            if "image" in mime_type: suffix = ".jpg"
            elif "audio" in mime_type: suffix = ".mp3"
            elif "video" in mime_type: suffix = ".mp4"
            elif "pdf" in mime_type: suffix = ".pdf"
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            temp_path = tmp.name

        # 2. Upload para Google AI Studio
        logger.info(f"Uploading file {temp_path} to Gemini...")
        print(f"Uploading {mime_type} to Gemini...")
        
        uploaded_file = genai.upload_file(temp_path, mime_type=mime_type)
        
        # 3. Esperar processamento (Apenas para vídeo/audio as vezes precisa, File API do Gemini tem estado)
        # Para vídeo/audio pesados, status pode ser PROCESSING.
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = genai.get_file(uploaded_file.name)
            
        if uploaded_file.state.name == "FAILED":
            raise Exception("Gemini File Upload Failed")

        # 4. Gerar Conteúdo
        # Usando Gemini 3.0 Pro Preview - State of the art reasoning
        model = genai.GenerativeModel("gemini-3-pro-preview") 
        
        logger.info("Generating content description with Gemini 3...")
        print("Asking Gemini 3 brain...")
        
        response = model.generate_content(
            [uploaded_file, prompt],
            request_options={"timeout": 600}
        )
        
        # 5. Cleanup (Opcional: Deletar arquivo do cloud se não for reusar)
        # genai.delete_file(uploaded_file.name) # Descomentar se quiser limpar na hora
        
        return response.text

    except Exception as e:
        logger.error(f"Erro no processamento Gemini: {e}")
        return f"Erro ao processar mídia com Gemini: {str(e)}"
    finally:
        # Limpar temp local
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

# Wrappers para manter compatibilidade com a chamada do main.py
def process_image(image_bytes: bytes) -> str:
    return process_media_with_gemini(image_bytes, "image/jpeg", "Descreva esta imagem em detalhes extremos para fins de busca e análise. Inclua textos, objetos, cores e sentimentos.")

def process_audio(audio_bytes: bytes, filename: str = "audio.mp3") -> str:
    return process_media_with_gemini(audio_bytes, "audio/mp3", "Transcreva este áudio completamente e gere um resumo dos pontos principais.", filename=filename)

def process_video(video_bytes: bytes) -> str:
    return process_media_with_gemini(video_bytes, "video/mp4", "Assista a este vídeo. 1. Transcreva o que é falado. 2. Descreva o que acontece visualmente frame a frame nos momentos chave.")
