from django.db import migrations
from django.contrib.auth.hashers import make_password
import os

def create_superuser(apps, schema_editor):
    User = apps.get_model("auth", "User")

    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    email = os.getenv("ADMIN_EMAIL", "")

    if not username or not password:
        return

    if not User.objects.filter(username=username).exists():
        User.objects.create(
            username=username,
            email=email,
            is_staff=True,
            is_superuser=True,
            is_active=True,
            password=make_password(password),
        )

class Migration(migrations.Migration):

    dependencies = [
    ("auth", "0012_alter_user_first_name_max_length"),
]

    operations = [
        migrations.RunPython(create_superuser),
    ]d