
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

# Importamos todas las funciones de una sola vez desde tu aplicación
from gestion_recuerdos.views import (
    index,
    login_google, 
    google_callback, 
    listar_fotos, 
    analizar_rostros_drive, 
    detectar_rostro_prueba,
    configurar_entorno_drive,
    guardar_rostro,
    home,
    galeria_familiar,
    eliminar_rostro,
    descartar_foto,
    ver_galeria,
)

urlpatterns = [
    # 🏠 INICIO
    path('', index, name='index'), 
    path('home/', home, name='home'),
    
    # ⚙️ ADMINISTRACIÓN
    path('admin/', admin.site.urls),
    
    # 🔑 AUTENTICACIÓN GOOGLE
    path('login-google/', login_google, name='login_google'),
    path('google/callback/', google_callback, name='google_callback'),
    
    # 📁 GESTIÓN DE DRIVE
    path('listar_fotos/', listar_fotos, name='listar_fotos'),
    path('organizar-drive/', configurar_entorno_drive, name='organizar_drive'),
    
    # 🧠 INTELIGENCIA ARTIFICIAL
    path('analizar/<str:file_id>/', analizar_rostros_drive, name='analizar_rostros'),
    path('probar-ia/', detectar_rostro_prueba, name='probar_ia'),
    path('guardar-rostro/', guardar_rostro, name='guardar_rostro'),
    path('descartar-foto/<str:file_id>/', descartar_foto, name='descartar_foto'),
    
    # 🖼️ GALERÍA (He dejado ver_galeria como la principal)
    path('ver_galeria/', ver_galeria, name='ver_galeria'), 
    path('galeria-familiar/', galeria_familiar, name='galeria_familiar'),
    path('eliminar-rostro/<int:rostro_id>/', eliminar_rostro, name='eliminar_rostro'),
]

# Configuración para ver archivos media (fotos) en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)