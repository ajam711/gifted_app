from django.contrib import admin

from gifts.models import Connection, ImportantDate, Item, Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "user")


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("person_a", "person_b", "created_at")


@admin.register(ImportantDate)
class ImportantDateAdmin(admin.ModelAdmin):
    list_display = ("person", "label", "month", "day", "year")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "added_by", "reaction", "created_at")
