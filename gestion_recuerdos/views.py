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
    return redirect('home')

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
#-------------------------------------------------------------------------------------------------
def obtener_fotos_recursivo(service, folder_id):
    """
    Función de apoyo: Busca fotos y entra en subcarpetas.
    """
    fotos_encontradas = []
    
    # Buscamos tanto carpetas como imágenes dentro del ID actual
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query, 
        fields="files(id, name, mimeType)"
    ).execute().get('files', [])

    for item in results:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            # Si es carpeta, entramos en ella (Recursividad)
            fotos_encontradas.extend(obtener_fotos_recursivo(service, item['id']))
        elif 'image/' in item['mimeType']:
            # Si es foto, la agregamos a la lista
            fotos_encontradas.append(item)
            
    return fotos_encontradas

def listar_fotos(request):
    creds_data = request.session.get('credentials')
    if not creds_data: return redirect('login_google')
    creds = Credentials(**creds_data)
    service = build('drive', 'v3', credentials=creds)

    # Buscamos la carpeta raíz 'Genealogia'
    query_f = "name = 'Genealogia' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    folders = service.files().list(q=query_f, fields="files(id)").execute().get('files', [])

    html = "<h1>Panel de Genealogía</h1>"
    if folders:
        folder_id = folders[0].get('id')
        
        # LLAMADA RECURSIVA: Aquí es donde sucede la magia
        items = obtener_fotos_recursivo(service, folder_id)
        
        html += f"<p>Escaneo completo. {len(items)} fotos encontradas en todas las subcarpetas.</p><ul>"
        for f in items:
            url = reverse('analizar_rostros', args=[f['id']])
            html += f'<li><a href="{url}">{f["name"]}</a></li>'
        html += "</ul>"
    else:
        html += f'<a href="{reverse("organizar_drive")}" style="background:green; color:white; padding:10px;">Organizar Drive Ahora</a>'
    html += f"<br><a href='{reverse('home')}' style='margin:20px; display:inline-block;'>⬅️ Volver al Menú</a>"
    return HttpResponse(html)


#---------------------------------------------------------------------------------------------------
# -----------------------------Importa tu modelo al principio del archivo views.py

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
    
 #-----------------------------------------------------------------------------------------------------   
def detectar_rostro_prueba(request):
    return HttpResponse("IA operativa")

@csrf_exempt # <--- Esto le dice a Django: "No pidas sello de seguridad aquí"
#---------------------------------------------------------------------------------------------------------
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
#-------------------------------------------------------------------------------------------------   
def galeria_familiar(request):
    """
    Busca todos los rostros guardados en la base de datos 
    y los muestra en una galería organizada por familiar.
    """
    rostros = RostroDetectado.objects.all().order_by('familiar')
    
    html = "<h1>🖼️ Galería de Rostros Familiares</h1>"
    html += "<div style='display:flex; flex-wrap:wrap; gap:20px; padding:20px;'>"
    
    for rostro in rostros:
        url_imagen = f"{settings.MEDIA_URL}{rostro.foto_recorte}"
        # NUEVO: Generamos la URL para eliminar
        url_eliminar = reverse('eliminar_rostro', args=[rostro.id])
        
        # Aquí es donde modificamos dentro de las comillas triples:
        html += f"""
            <div style='border:2px solid #673ab7; border-radius:15px; padding:15px; text-align:center; background:#f9f9f9; width:180px;'>
                <img src='{url_imagen}' style='width:150px; height:150px; object-fit:cover; border-radius:10px;'>
                <h3 style='color:#333; margin:10px 0 5px 0;'>{rostro.familiar.nombre}</h3>
                <span style='font-size:0.8em; color:#666;'>ID Drive: {rostro.drive_file_id}</span>
                <br><br>
                <a href='{url_eliminar}' style='color:red; text-decoration:none; font-weight:bold;'>[❌ Eliminar]</a>
            </div>
        """
    
    html += "</div>"
    html += f"<br><a href='{reverse('home')}' style='margin:20px; display:inline-block;'>⬅️ Volver al Menú</a>"
    
    return HttpResponse(html)
#------------------------------------------------------------------------------------------

def eliminar_rostro(request, rostro_id):
    """
    Línea por línea:
    1. Busca el registro del rostro en la BD usando su ID.
    2. Obtiene la ruta física de la imagen en tu carpeta media.
    3. Si el archivo existe en la carpeta, lo borra del disco duro.
    4. Borra el registro de la base de datos.
    5. Te redirige de vuelta a la galería.
    """
    import os
    rostro = RostroDetectado.objects.get(id=rostro_id)
    ruta_imagen = os.path.join(settings.MEDIA_ROOT, rostro.foto_recorte.name)

    # Borrar archivo físico
    if os.path.exists(ruta_imagen):
        os.remove(ruta_imagen)

    # Borrar registro en BD
    rostro.delete()
    
    return redirect('galeria')
#------------------------------------------------------------------------------------


    
def home(request):
    """
    Esta función será tu centro de control.
    """
    html = """
    <h1>Sistema de Genealogía IA</h1>
    <div style='display: flex; gap: 20px;'>
        <a href='/ver-fotos/' style='padding:20px; background:blue; color:white; text-decoration:none; border-radius:10px;'>
            📸 Ver Fotos de Drive
        </a>
        <a href='/admin/' style='padding:20px; background:orange; color:white; text-decoration:none; border-radius:10px;'>
            ⚙️ Gestionar Base de Datos (Admin)
        </a>
        <a href='/galeria/' style='padding:20px; background:purple; color:white; text-decoration:none; border-radius:10px;'>
            🖼️ Ir a la Galería Familiar
        </a>
    </div>
    """
    return HttpResponse(html)


