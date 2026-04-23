from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jsquared_app', '0014_orderitem_supplier'),
    ]

    operations = [
        migrations.AddField(
            model_name='suppliertransaction',
            name='quantity',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='suppliertransaction',
            name='unit_price',
            field=models.FloatField(default=0),
        ),
        migrations.AlterField(
            model_name='suppliertransaction',
            name='payment_status',
            field=models.CharField(choices=[('Pending', 'Pending'), ('Completed', 'Completed')], default='Pending', max_length=10),
        ),
    ]
