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

from django.shortcuts import render
from .models import Familiar, RostroDetectado


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
#-------------------------------------------------------------------------------------------------

def index(request):
    """Función para renderizar el menú principal del sistema"""
    return render(request, 'gestion_recuerdos/index.html')




#-------------------------------------------------------------------------------------------------

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
        return redirect('listar_fotos')
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
#_____________________________________________________________________

def listar_fotos(request):
    # 1. IMPORTACIONES DE SEGURIDAD
    # 'Credentials' verifica quién eres y 'build' construye la conexión con Google
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    # 2. VERIFICACIÓN: Revisa si el usuario ya inició sesión en Google
    creds_data = request.session.get('credentials')
    if not creds_data: 
        return redirect('login_google')
    
    # 3. CONEXIÓN: Se conecta a la API de Drive v3
    creds = Credentials(**creds_data)
    service = build('drive', 'v3', credentials=creds)

    # 4. BÚSQUEDA: Localiza la carpeta raíz llamada 'Genealogia'
    query_f = "name = 'Genealogia' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    folders = service.files().list(q=query_f, fields="files(id)").execute().get('files', [])

    fotos_pendientes = []
    folder_encontrada = False

    if folders:
        folder_encontrada = True
        folder_id = folders[0].get('id')
        
        # 5. RECURSIVIDAD: Obtiene todas las fotos dentro de 'Genealogia' y sus subcarpetas
        items_drive = obtener_fotos_recursivo(service, folder_id)
        
        # 6. FILTRO DE BASE DE DATOS: 
        # Trae todos los IDs de fotos que YA guardamos para no repetirlas
        ids_ya_procesados = RostroDetectado.objects.values_list('drive_file_id', flat=True)
        
        # Solo metemos en la lista las fotos cuyo ID NO esté en la base de datos
        fotos_pendientes = [f for f in items_drive if f['id'] not in ids_ya_procesados]

    # 7. RENDERIZADO: Envía los resultados al archivo HTML
    # Aquí es donde conectamos la función con el archivo físico listar_fotos.html
    return render(request, 'gestion_recuerdos/listar_fotos.html', {
        'fotos': fotos_pendientes,
        'folder_encontrada': folder_encontrada
    })


# ---------------Importa tu modelo al principio del archivo views.py


from .models import Familiar 


def analizar_rostros_drive(request, file_id):
    # Importaciones necesarias dentro de la función (o puedes ponerlas arriba en el archivo)
    import os, cv2, numpy as np, io, shutil
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from google.oauth2.credentials import Credentials
    from django.conf import settings
    from .models import Familiar, RostroDetectado

    # 1. PREPARACIÓN: Definimos y limpiamos la carpeta temporal de caras
    # Esto asegura que no veas rostros de la foto anterior.
    carpeta_temp = os.path.join(settings.MEDIA_ROOT, 'temp_caras')
    if os.path.exists(carpeta_temp): 
        shutil.rmtree(carpeta_temp)
    os.makedirs(carpeta_temp, exist_ok=True)

    try:
        # 2. CONEXIÓN: Recuperamos las credenciales de la sesión para Google Drive
        creds_data = request.session.get('credentials')
        creds = Credentials(**creds_data)
        service = build('drive', 'v3', credentials=creds)

        # 3. DESCARGA: Bajamos la imagen de Drive directamente a la memoria (BytesIO)
        request_download = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_download)
        done = False
        while not done:
            status, done = downloader.next_chunk()

        # 4. PROCESAMIENTO IA: Convertimos los bytes en una imagen que OpenCV entienda
        img_array = np.frombuffer(fh.getvalue(), np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Cargamos el modelo de detección de rostros (Haar Cascade)
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        # Detectamos las caras (ajustamos a 7 para evitar falsos positivos)
        caras = face_cascade.detectMultiScale(gris, 1.1, 7)

        # 5. RECORTE: Guardamos cada cara detectada como un archivo temporal .jpg
        for i, (x, y, w, h) in enumerate(caras):
            cara_recortada = img[y:y+h, x:x+w]
            cv2.imwrite(os.path.join(carpeta_temp, f"cara_{i}.jpg"), cara_recortada)

        # 6. RETORNO: Enviamos los datos al template 'analizar.html'
        context = {
            'file_id': file_id,
            'familiares': Familiar.objects.all(), # Para el menú desplegable de nombres
            'total_caras': range(len(caras)),      # Para que el HTML sepa cuántas caras mostrar
            'MEDIA_URL': settings.MEDIA_URL,
        }
        return render(request, 'gestion_recuerdos/analizar.html', context)

    except Exception as e:
        return HttpResponse(f"Hubo un error al analizar la imagen: {str(e)}")
    
 #-----------------------------------------------------------------------------------------------------   
def detectar_rostro_prueba(request):
    return HttpResponse("IA operativa")

@csrf_exempt # <--- Esto le dice a Django: "No pidas sello de seguridad aquí"
#---------------------------------------------------------------------------------------------------------
def guardar_rostro(request):
    if request.method == 'POST':
        ruta_permanente = os.path.join(settings.MEDIA_ROOT, 'rostros_permanentes')
        os.makedirs(ruta_permanente, exist_ok=True)
        
        # 1. Recuperamos el ID real de la foto de Drive que enviamos en el paso anterior
        drive_id_real = request.POST.get('drive_file_id', 'ID_DESCONOCIDO')

        for key, value in request.POST.items():
            if key.startswith('familiar_') and value:
                indice = key.split('_')[1]
                nombre_archivo_temp = f"cara_{indice}.jpg"
                ruta_temp = os.path.join(settings.MEDIA_ROOT, 'temp_caras', nombre_archivo_temp)
                
                if os.path.exists(ruta_temp):
                    familiar = Familiar.objects.get(id=value)
                    # Usamos el drive_id en el nombre para que el archivo sea único
                    nombre_final = f"{drive_id_real}_{indice}.jpg"
                    
                    # 2. EFICIENCIA: update_or_create
                    # Si ya existe un rostro para este ID de Drive y este índice, lo actualiza.
                    # Si no, lo crea. Esto ELIMINA los duplicados en la galería.
                    nuevo_rostro, created = RostroDetectado.objects.update_or_create(
                        drive_file_id=drive_id_real,
                        # Usamos el índice para diferenciar si hay varios rostros en una misma foto
                        foto_recorte=f"rostros_permanentes/{nombre_final}", 
                        defaults={
                            'familiar': familiar,
                        }
                    )
                    
                    # 3. Solo movemos el archivo físico si el registro es nuevo
                    ruta_final = os.path.join(ruta_permanente, nombre_final)
                    if not os.path.exists(ruta_final):
                        shutil.move(ruta_temp, ruta_final)

        return HttpResponse("<h2>¡Guardado con éxito!</h2><a href='/listar_fotos/'>Volver</a>")
#-------------------------------------------------------------------------------------------------   
def galeria_familiar(request):
    rostros = RostroDetectado.objects.all().order_by('familiar')
    # Añadimos 'MEDIA_URL' al diccionario para que el HTML lo reconozca
    return render(request, 'gestion_recuerdos/galeria.html', {
        'rostros': rostros,
        'MEDIA_URL': settings.MEDIA_URL  # <--- Esto es la clave
    })
#------------------------------------------------------------------------------------------


def descartar_foto(request, file_id):
    """
    Funcionalidad:
    1. Crea un registro en la tabla RostroDetectado.
    2. Le asigna un 'Familiar' genérico (puedes crear uno llamado 'Descartado' en el admin).
    3. Al tener el drive_file_id registrado, la función listar_fotos ya no la mostrará.
    """
    # Buscamos o creamos un familiar llamado "Descartado" para no romper la estructura
    familiar_descarte, _ = Familiar.objects.get_or_create(nombre="DESCARTADO")
    
    RostroDetectado.objects.update_or_create(
        drive_file_id=file_id,
        defaults={
            'familiar': familiar_descarte,
            'foto_recorte': 'descarte.jpg' # No necesitamos archivo físico real
        }
    )
    return redirect('listar_fotos')

#---------------------------------------------------------------------------------------------------------

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
    
    return redirect('ver_galeria')
#------------------------------------------------------------------------------------

def home(request):
    """
    Renderiza el menú principal usando el template home.html.
    """
    return render(request, 'gestion_recuerdos/home.html')
    
#---------------------------------------------------------------------------------------------------------

def ver_galeria(request):
    """
    Función que consulta la base de datos para obtener todos los 
    familiares y sus respectivos rostros detectados.
    """
    # Obtenemos todos los familiares y cargamos sus rostros relacionados
    familiares = Familiar.objects.prefetch_related('rostros').all()
    
    return render(request, 'gestion_recuerdos/galeria.html', {
        'familiares': familiares
    })



