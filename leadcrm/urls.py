from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('leads.urls')),
    path('chatbot/', include('chatbot.urls')),   
    path('capi/', include('capi.urls')),         # Conversion API Center
    path('sales-ai/', include('salesai.urls')),  # AI Sales Assistant + Offer Matching
    path('marketing/', include('marketing.urls')),   # ← add (views come later)
    
    
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)



