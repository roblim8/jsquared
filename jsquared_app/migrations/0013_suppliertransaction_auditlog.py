
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):

    dependencies = [
        ('jsquared_app', '0012_account'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierTransaction',
            fields=[
                ('transaction_id', models.AutoField(primary_key=True, serialize=False)),
                ('item_name', models.CharField(blank=True, max_length=100, null=True)),
                ('transaction_date', models.DateField(default=django.utils.timezone.now)),
                ('transaction_amount', models.FloatField(default=0)),
                ('payment_status', models.CharField(choices=[('Paid', 'Paid'), ('Unpaid', 'Unpaid')], default='Unpaid', max_length=10)),
                ('notes', models.TextField(blank=True, null=True)),
                ('meat', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='jsquared_app.meatitem')),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='jsquared_app.supplier')),
            ],
            options={'db_table': 'SUPPLIER_TRANSACTION', 'ordering': ['-transaction_date', '-transaction_id']},
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('audit_log_id', models.AutoField(primary_key=True, serialize=False)),
                ('username', models.CharField(blank=True, max_length=150, null=True)),
                ('action', models.CharField(max_length=20)),
                ('path', models.CharField(max_length=255)),
                ('method', models.CharField(default='GET', max_length=10)),
                ('model_name', models.CharField(blank=True, max_length=100, null=True)),
                ('object_repr', models.CharField(blank=True, max_length=200, null=True)),
                ('details', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('staff', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='jsquared_app.staff')),
            ],
            options={'db_table': 'AUDIT_LOG', 'ordering': ['-created_at', '-audit_log_id']},
        ),
    ]
