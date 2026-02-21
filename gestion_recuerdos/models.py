from django.db import models

# --- IMPORTACIONES EXPLICADAS ---
# models: Herramienta de Django para crear las tablas de la base de datos sin usar SQL.

class Familiar(models.Model):
    """Representa a una persona real de tu árbol genealógico."""
    nombre = models.CharField(max_length=150, verbose_name="Nombre")
    apellido = models.CharField(max_length=100, blank=True, null=True)
    
    PARENTESCO_CHOICES = [
        ('HIJO', 'Hijo/a'),
        ('PADRE', 'Padre/Madre'),
        ('PAREJA', 'Pareja'),
        ('HERMANO', 'Hermano/a'),
    ]
    parentesco = models.CharField(max_length=10, choices=PARENTESCO_CHOICES, blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    biografia = models.TextField(blank=True, help_text="Notas históricas sobre este familiar")
    
    # Identificador para la IA
    face_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    def __str__(self):
        return f"{self.nombre} {self.apellido if self.apellido else ''}"

class RostroDetectado(models.Model):
    """Guarda cada recorte facial y lo vincula a un familiar."""
    # ForeignKey: Vincula este rostro con un Familiar de la tabla de arriba.
    familiar = models.ForeignKey(Familiar, on_delete=models.CASCADE, related_name='rostros', null=True, blank=True)
    
    # ImageField: Gestiona la ruta del archivo en la carpeta media/rostros_permanentes/
    foto_recorte = models.ImageField(upload_to='rostros_permanentes/')
    
    # drive_file_id: Para recordar de qué foto de Google Drive salió este recorte.
    drive_file_id = models.CharField(max_length=255)
    
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        nombre = self.familiar.nombre if self.familiar else "No identificado"
        return f"Rostro de {nombre} (Drive ID: {self.drive_file_id})"
