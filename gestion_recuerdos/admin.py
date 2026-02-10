from django.contrib import admin
from .models import Familiar
from .models import Familiar, RostroDetectado

# Register your models here.

admin.site.register(Familiar)
admin.site.register(RostroDetectado)
