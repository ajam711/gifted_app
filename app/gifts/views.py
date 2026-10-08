from django.contrib import messages
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from domain import (
    Conflict,
    ConflictReason,
    Forbidden,
    Invalid,
    NotFound,
    OwnerItemView,
    ShopperItemView,
)

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


def _grouped_shopper_items(
    items: list[ShopperItemView], shopper_id: int
) -> dict[str, list[ShopperItemView]]:
    """Their list groups, derived from the row like ``domain.views`` predicates.

    Disliked items get their own group with no actions, so a suggestion
    the owner turned down does not just vanish from the shopper's view.
    """

    groups: dict[str, list[ShopperItemView]] = {
        "open": [],
        "yours": [],
        "waiting": [],
        "given": [],
        "disliked": [],
    }
    for item in items:
        if item.given_at is not None:
            groups["given"].append(item)
        elif item.claimed_by_id == shopper_id:
            groups["yours"].append(item)
        elif item.reaction == "liked" and item.claimed_by_id is None:
            groups["open"].append(item)
        elif item.reaction is None:
            groups["waiting"].append(item)
        elif item.reaction == "disliked":
            groups["disliked"].append(item)
    return groups


@person_required
def their_list(request):
    store = DjangoStore()
    partner = _partner(store, request.person.id)
    if partner is None:
        raise Http404("Not found")
    groups = _grouped_shopper_items(
        store.shopper_items(request.person.id), request.person.id
    )
    # ``?item=<id>`` after a claim / unclaim / give: briefly highlight that card.
    highlight = request.GET.get("item", "")
    return render(
        request,
        "gifts/their_list.html",
        {
            "person": request.person,
            "partner": partner,
            "has_items": any(groups.values()),
            "highlight": int(highlight) if highlight.isdigit() else None,
            **groups,
        },
    )


# Shopper-only refusal messages. Only the partner can reach these (the
# domain checks the actor before the item's state), so they may talk
# about claims. ``{name}`` is the item, ``{partner}`` the list owner.
_CONFLICT_MESSAGES: dict[ConflictReason, str] = {
    "yours": "You already claimed {name}. It's under Yours.",
    "claimed": "{name} is already claimed.",
    "not_claimed": "You haven't claimed {name}.",
    "not_liked": "{partner} hasn't liked {name} yet.",
    "disliked": "{partner} no longer wants {name}. It's under Not for {partner}.",
    "given": "{name} was already marked given.",
}


def _shopper_action(request, item_id: int, action: str, done: str):
    """Claim / unclaim / give. Shopper-only, so every result goes to Their list.

    ``done`` is the success message template, with ``{name}``. A
    ``Conflict`` says why, and the item it was about is highlighted.
    ``Forbidden`` is only reachable by the owner typing a URL by hand;
    it gets one generic message whatever the item's state.
    """

    store = DjangoStore()
    their_list_url = f"{reverse('their_list')}?item={item_id}"
    try:
        saved = getattr(store, action)(request.person.id, item_id)
    except Conflict as refusal:
        item = store.shopper_item(request.person.id, item_id)
        partner = _partner(store, request.person.id)
        messages.warning(
            request,
            _CONFLICT_MESSAGES[refusal.reason].format(
                name=item.name, partner=partner.name
            ),
        )
        return redirect(their_list_url)
    except (Forbidden, Invalid):
        messages.error(request, "Could not save that.")
        return redirect("their_list")
    except NotFound:
        raise Http404("Not found") from None
    messages.success(request, done.format(name=saved.name))
    return redirect(their_list_url)


@person_required
@require_POST
def claim_item(request, item_id: int):
    return _shopper_action(request, item_id, "claim", "Claimed {name}.")


@person_required
@require_POST
def unclaim_item(request, item_id: int):
    return _shopper_action(request, item_id, "unclaim", "Released {name}.")


@person_required
@require_POST
def give_item(request, item_id: int):
    return _shopper_action(request, item_id, "give", "Marked {name} as given.")


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
