from django.contrib import messages
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from domain import Forbidden, Invalid, NotFound, OwnerItemView

from gifts.auth import person_required
from gifts.forms import AddItemForm
from gifts.models import Person
from gifts.store import DjangoStore


def _partner(store: DjangoStore, person_id: int) -> Person | None:
    try:
        partner_id = store.partner_id(person_id)
    except NotFound:
        return None
    return Person.objects.get(pk=partner_id)


@person_required
def home(request):
    store = DjangoStore()
    partner = _partner(store, request.person.id)
    return render(
        request,
        "gifts/home.html",
        {"person": request.person, "partner": partner},
    )


def _grouped_owner_items(
    items: list[OwnerItemView],
) -> tuple[list[OwnerItemView], list[OwnerItemView], list[OwnerItemView]]:
    """Pending, liked, disliked — groups are derived from ``reaction``, not a status column."""

    pending: list[OwnerItemView] = []
    liked: list[OwnerItemView] = []
    disliked: list[OwnerItemView] = []
    for item in items:
        if item.reaction is None:
            pending.append(item)
        elif item.reaction == "liked":
            liked.append(item)
        else:
            disliked.append(item)
    return pending, liked, disliked


@person_required
def my_list(request):
    store = DjangoStore()
    pending, liked, disliked = _grouped_owner_items(
        store.owner_items(request.person.id)
    )
    return render(
        request,
        "gifts/my_list.html",
        {
            "person": request.person,
            "pending": pending,
            "liked": liked,
            "disliked": disliked,
        },
    )


@person_required
@require_POST
def react_item(request, item_id: int):
    """Owner likes or dislikes. Target is My list, not a shopper action."""

    reaction = (request.POST.get("reaction") or "").strip()
    store = DjangoStore()
    try:
        saved = store.react(request.person.id, item_id, reaction)
    except Invalid:
        messages.error(request, "Could not save that.")
        return redirect("my_list")
    except Forbidden:
        return HttpResponseForbidden("Not allowed")
    except NotFound:
        raise Http404("Not found") from None
    messages.success(request, f"Saved {saved.name}.")
    return redirect("my_list")


@person_required
def their_list(request):
    store = DjangoStore()
    partner = _partner(store, request.person.id)
    if partner is None:
        raise Http404("Not found")
    items = store.shopper_items(request.person.id)
    return render(
        request,
        "gifts/their_list.html",
        {"person": request.person, "partner": partner, "items": items},
    )


@person_required
def add_item(request, list_key: str):
    """List identity is the URL this screen was opened from, not a picker."""

    store = DjangoStore()
    person = request.person
    if list_key == "mine":
        owner_id = person.id
        heading = "Add to your list"
        show_giver_note = False
        success_url = reverse("my_list")
        cancel_url = reverse("my_list")
    else:
        partner = _partner(store, person.id)
        if partner is None:
            raise Http404("Not found")
        owner_id = partner.id
        heading = f"Add to {partner.name}'s list"
        show_giver_note = True
        success_url = reverse("their_list")
        cancel_url = reverse("their_list")

    form = AddItemForm(request.POST or None, show_giver_note=show_giver_note)
    if request.method == "POST" and form.is_valid():
        try:
            saved = store.add_item(person.id, owner_id, **form.item_kwargs())
        except Invalid as error:
            form.add_error("name", str(error))
        except Forbidden:
            return HttpResponseForbidden("Not allowed")
        except NotFound:
            raise Http404("Not found") from None
        else:
            messages.success(request, f"Saved {saved.name}.")
            return redirect(success_url)

    return render(
        request,
        "gifts/add_item.html",
        {
            "form": form,
            "heading": heading,
            "cancel_url": cancel_url,
            "show_giver_note": show_giver_note,
        },
    )
