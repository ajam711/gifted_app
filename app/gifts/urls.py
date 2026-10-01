from django.urls import path

from gifts import views

urlpatterns = [
    path("", views.home, name="home"),
    path("my-list/", views.my_list, name="my_list"),
    path("my-list/add/", views.add_item, {"list_key": "mine"}, name="add_my_item"),
    path("my-list/<int:item_id>/react/", views.react_item, name="react_item"),
    path("their-list/", views.their_list, name="their_list"),
    path(
        "their-list/add/",
        views.add_item,
        {"list_key": "theirs"},
        name="add_their_item",
    ),
]
