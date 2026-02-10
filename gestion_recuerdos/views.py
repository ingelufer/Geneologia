import os
import io
import json
import numpy as np
import cv2
import shutil
from django.shortcuts import redirect, render
from django.http import HttpResponse
from django.conf import settings
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
 # RostroFamiliar es la tabla que guarda la unión
from .models import Familiar, RostroDetectado

# Librerías de Google
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials

# --- CONFIGURACIÓN ---
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
SCOPES = ['https://www.googleapis.com/auth/drive']

def login_google(request):
    ruta_json = os.path.join(settings.BASE_DIR, 'client_secrets.json')
    flow = Flow.from_client_secrets_file(ruta_json, scopes=SCOPES, redirect_uri='http://127.0.0.1:8000/google/callback/')
    auth_url, state = flow.authorization_url(prompt='consent')
    request.session['oauth_state'] = state
    return redirect(auth_url)

def google_callback(request):
    ruta_json = os.path.join(settings.BASE_DIR, 'client_secrets.json')
    flow = Flow.from_client_secrets_file(ruta_json, scopes=SCOPES, redirect_uri='http://127.0.0.1:8000/google/callback/')
    flow.fetch_token(authorization_response=request.build_absolute_uri())
    credentials = flow.credentials
    request.session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    return redirect('ver_fotos')

def configurar_entorno_drive(request):
    try:
        creds_data = request.session.get('credentials')
        if not creds_data: return redirect('login_google')
        creds = Credentials(**creds_data)
        service = build('drive', 'v3', credentials=creds)

        query_folder = "name = 'Genealogia' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        folders = service.files().list(q=query_folder, fields="files(id)").execute().get('files', [])

        if not folders:
            folder_metadata = {'name': 'Genealogia', 'mimeType': 'application/vnd.google-apps.folder'}
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            folder_id = folder.get('id')
        else:
            folder_id = folders[0].get('id')

        query_fotos = "mimeType contains 'image/' and 'root' in parents and trashed = false"
        fotos = service.files().list(q=query_fotos, fields="files(id, parents)").execute().get('files', [])

        for foto in fotos:
            service.files().update(
                fileId=foto['id'],
                addParents=folder_id,
                removeParents=",".join(foto.get('parents')),
                fields='id, parents'
            ).execute()
        return redirect('ver_fotos')
    except Exception as e:
        return HttpResponse(f"Error al organizar: {str(e)}")

def listar_fotos(request):
    creds_data = request.session.get('credentials')
    if not creds_data: return redirect('login_google')
    creds = Credentials(**creds_data)
    service = build('drive', 'v3', credentials=creds)

    query_f = "name = 'Genealogia' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    folders = service.files().list(q=query_f, fields="files(id)").execute().get('files', [])

    html = "<h1>Panel de Genealogía</h1>"
    if folders:
        folder_id = folders[0].get('id')
        
        q_fotos = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed = false"
        items = service.files().list(q=q_fotos, fields="files(id, name)").execute().get('files', [])
        html += f"<p>Carpeta detectada. {len(items)} fotos listas.</p><ul>"
        for f in items:
            url = reverse('analizar_rostros', args=[f['id']])
            html += f'<li><a href="{url}">{f["name"]}</a></li>'
        html += "</ul>"
    else:
        html += f'<a href="{reverse("organizar_drive")}" style="background:green; color:white; padding:10px;">Organizar Drive Ahora</a>'
    return HttpResponse(html)

# Importa tu modelo al principio del archivo views.py
from .models import Familiar 

def analizar_rostros_drive(request, file_id):
    """
    Línea por línea:
    1. Limpia y prepara la carpeta temporal para los nuevos recortes.
    2. Descarga la imagen original desde Google Drive.
    3. La IA detecta las coordenadas de los rostros.
    4. CONSULTA: Trae la lista de todos los familiares de tu base de datos (Luis, Paola, Valery).
    5. HTML: Genera un formulario para cada rostro detectado que permite elegir quién es.
    """
    import os, cv2, numpy as np, io, shutil
    carpeta_temp = os.path.join(settings.MEDIA_ROOT, 'temp_caras')
    if os.path.exists(carpeta_temp): shutil.rmtree(carpeta_temp)
    os.makedirs(carpeta_temp, exist_ok=True)

    try:
        # --- Lógica de Google Drive ---
        creds_data = request.session.get('credentials')
        creds = Credentials(**creds_data)
        service = build('drive', 'v3', credentials=creds)
        request_download = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_download)
        done = False
        while not done: _, done = downloader.next_chunk()

        # --- Lógica de IA ---
        img_array = np.frombuffer(fh.getvalue(), np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        caras = face_cascade.detectMultiScale(gris, 1.1, 4)

        # --- Lógica de Base de Datos ---
        # Obtenemos los familiares que registraste antes para el menú desplegable
        familiares = Familiar.objects.all() 

        html = "<h2>Resultados del Análisis</h2>"
        html += "<form method='POST' action='/guardar-rostro/'>"
        html += "{% csrf_token %}" # Seguridad de Django
        html += "<div style='display:flex; flex-wrap:wrap; gap:20px;'>"

        for i, (x, y, w, h) in enumerate(caras):
            nombre_cara = f"cara_{i}.jpg"
            cv2.imwrite(os.path.join(carpeta_temp, nombre_cara), img[y:y+h, x:x+w])
            url_web = f"{settings.MEDIA_URL}temp_caras/{nombre_cara}"
            
            html += f"""
                <div style='text-align:center; border:1px solid #ddd; padding:10px; border-radius:10px;'>
                    <img src='{url_web}' style='width:150px; border-radius:5px;'>
                    <br><br>
                    <label>¿Quién es?</label><br>
                    <select name='familiar_{i}' style='margin-bottom:10px;'>
                        <option value=''>--- Seleccionar ---</option>
                        {"".join([f"<option value='{f.id}'>{f.nombre}</option>" for f in familiares])}
                      
                    </select>
                </div>
            """

        html += "</div><br><button type='submit' style='padding:10px 20px; background:green; color:white; border:none; border-radius:5px;'>Guardar todos en la BD</button></form>"
        html += "<br><a href='/ver-fotos/'>Volver sin guardar</a>"

        # Nota: Como estamos usando HttpResponse directo, el {% csrf_token %} no funcionará 
        # sin un template. Por ahora, para probar, usaremos una versión simplificada.
        return HttpResponse(html.replace("{% csrf_token %}", ""))

    except Exception as e:
        return HttpResponse(f"Error: {str(e)}")
def detectar_rostro_prueba(request):
    return HttpResponse("IA operativa")

@csrf_exempt # <--- Esto le dice a Django: "No pidas sello de seguridad aquí"

def guardar_rostro(request):
    if request.method == 'POST':
        # Ruta permanente según tu modelo: media/rostros_permanentes/
        ruta_permanente = os.path.join(settings.MEDIA_ROOT, 'rostros_permanentes')
        os.makedirs(ruta_permanente, exist_ok=True)

        for key, value in request.POST.items():
            if key.startswith('familiar_') and value:
                indice = key.split('_')[1]
                nombre_archivo_temp = f"cara_{indice}.jpg"
                ruta_temp = os.path.join(settings.MEDIA_ROOT, 'temp_caras', nombre_archivo_temp)
                
                if os.path.exists(ruta_temp):
                    familiar = Familiar.objects.get(id=value)
                    nombre_final = f"{familiar.nombre}_{nombre_archivo_temp}"
                    
                    # Guardamos el registro en la base de datos usando RostroDetectado
                    nuevo_rostro = RostroDetectado.objects.create(
                        familiar=familiar,
                        foto_recorte=f"rostros_permanentes/{nombre_final}",
                        drive_file_id="ID_DESCONOCIDO" # Aquí podrías pasar el file_id real
                    )
                    
                    # Movemos el archivo físico
                    ruta_final = os.path.join(ruta_permanente, nombre_final)
                    shutil.move(ruta_temp, ruta_final)

        return HttpResponse("<h2>¡Guardado con éxito!</h2><a href='/ver-fotos/'>Volver</a>")