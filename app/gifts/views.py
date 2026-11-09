from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
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
from gifts.forms import AddItemForm, KeepPersonForm
from gifts.models import Item, Person
from gifts.store import DjangoStore


def _names(person_ids) -> dict[int, str]:
    ids = {pk for pk in person_ids if pk}
    if not ids:
        return {}
    return dict(Person.objects.filter(pk__in=ids).values_list("id", "name"))


def _shop_location(
    store: DjangoStore, actor_id: int, owner_id: int, item_id: int | None = None
) -> str:
    """URL of a shoppable list. One connection keeps the old ``/their-list/`` path."""

    if store.partner_ids(actor_id) == [owner_id]:
        base = reverse("their_list")
    else:
        base = reverse("shop_list", args=[owner_id])
    if item_id is None:
        return base
    return f"{base}?item={item_id}"


def _resolve_shop_owner(
    store: DjangoStore, actor_id: int, owner_id: int | None
) -> int:
    """The list this screen was opened for. Missing or not shoppable is 404."""

    if owner_id is None:
        try:
            return store.partner_id(actor_id)
        except NotFound:
            raise Http404("Not found") from None
    try:
        allowed = store.can_shop(actor_id, owner_id)
    except NotFound:
        raise Http404("Not found") from None
    if not allowed:
        raise Http404("Not found")
    return owner_id


def _home_context(request, keep_form: KeepPersonForm) -> dict:
    store = DjangoStore()
    today = timezone.localdate()
    person = request.person
    # Badges come from the domain payload: a needs-reaction count and the
    # soonest date among people reached through Connection. Neither value
    # is derived from claim columns. Each door shows that person's date.
    doors = []
    for other_id in store.partner_ids(person.id):
        doors.append(
            {
                "person": Person.objects.get(pk=other_id),
                "upcoming": store.next_important_date(other_id, today),
                "url": _shop_location(store, person.id, other_id),
            }
        )
    return {
        "person": person,
        "badges": store.home_badges(person.id, today),
        "shop_doors": doors,
        "keep_form": keep_form,
    }


@person_required
def home(request):
    return render(
        request, "gifts/home.html", _home_context(request, KeepPersonForm())
    )


@person_required
@require_POST
def keep_person(request):
    """Start a private list for someone who will not sign in."""

    form = KeepPersonForm(request.POST)
    if not form.is_valid():
        return render(request, "gifts/home.html", _home_context(request, form))
    person = DjangoStore().keep_person(
        request.person.id,
        form.cleaned_data["name"],
        form.cleaned_data["email"],
    )
    messages.success(request, f"Keeping a list for {person.name}.")
    return redirect("shop_list", owner_id=person.id)


def _grouped_owner_items(
    items: list[OwnerItemView],
) -> tuple[
    list[OwnerItemView], list[OwnerItemView], list[OwnerItemView], list[OwnerItemView]
]:
    """Pending, liked, disliked, received — derived from the row, not a status column.

    Received comes first: once the owner has it, it leaves the other groups.
    """

    pending: list[OwnerItemView] = []
    liked: list[OwnerItemView] = []
    disliked: list[OwnerItemView] = []
    received: list[OwnerItemView] = []
    for item in items:
        if item.received_at is not None:
            received.append(item)
        elif item.reaction is None:
            pending.append(item)
        elif item.reaction == "liked":
            liked.append(item)
        else:
            disliked.append(item)
    received.sort(key=lambda item: item.received_at, reverse=True)
    return pending, liked, disliked, received


@person_required
def my_list(request):
    store = DjangoStore()
    items = store.owner_items(request.person.id)
    pending, liked, disliked, received = _grouped_owner_items(items)
    return render(
        request,
        "gifts/my_list.html",
        {
            "person": request.person,
            "pending": pending,
            "liked": liked,
            "disliked": disliked,
            "received": received,
            "giver_names": _names(item.received_from_id for item in received),
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


def _owner_action(request, action, done: str):
    """Receive / unreceive on My list. Same refusal shape as ``react_item``."""

    try:
        saved = action(DjangoStore())
    except Invalid:
        messages.error(request, "Could not save that.")
        return redirect("my_list")
    except Forbidden:
        return HttpResponseForbidden("Not allowed")
    except NotFound:
        raise Http404("Not found") from None
    messages.success(request, done.format(name=saved.name))
    return redirect("my_list")


@person_required
@require_POST
def receive_item(request, item_id: int):
    """Owner got it from someone else. The giver's name is optional."""

    received_from = request.POST.get("received_from") or None
    return _owner_action(
        request,
        lambda store: store.receive(request.person.id, item_id, received_from),
        "Moved {name} to Received.",
    )


@person_required
@require_POST
def unreceive_item(request, item_id: int):
    return _owner_action(
        request,
        lambda store: store.unreceive(request.person.id, item_id),
        "Moved {name} back to your list.",
    )


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
        "others": [],
        "waiting": [],
        "given": [],
        "disliked": [],
        "elsewhere": [],
    }
    for item in items:
        if item.given_at is not None:
            groups["given"].append(item)
        elif item.received_at is not None:
            groups["elsewhere"].append(item)
        elif item.claimed_by_id == shopper_id:
            groups["yours"].append(item)
        elif item.claimed_by_id is not None:
            groups["others"].append(item)
        elif item.reaction == "liked" and item.claimed_by_id is None:
            groups["open"].append(item)
        elif item.reaction is None:
            groups["waiting"].append(item)
        elif item.reaction == "disliked":
            groups["disliked"].append(item)
    return groups


@person_required
def their_list(request, owner_id: int | None = None):
    store = DjangoStore()
    owner_id = _resolve_shop_owner(store, request.person.id, owner_id)
    partner = Person.objects.get(pk=owner_id)
    groups = _grouped_shopper_items(
        store.shopper_items(request.person.id, owner_id), request.person.id
    )
    from_them: list[OwnerItemView] = []
    if partner.user_id is None:
        from_them = [
            item
            for item in store.owner_items(request.person.id)
            if item.received_at is not None and item.received_from == partner.name
        ]
        from_them.sort(key=lambda item: item.received_at, reverse=True)
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
            "names": _names(
                item.claimed_by_id for group in groups.values() for item in group
            ),
            "from_them": from_them,
            "add_url": reverse("add_shop_item", args=[owner_id]),
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
    "received": "{partner} already got {name} from someone else.",
}


def _shopper_action(request, item_id: int, action: str, done: str):
    """Claim / unclaim / give. Shopper-only, so every result goes to Their list.

    ``done`` is the success message template, with ``{name}`` and
    ``{partner}``. A
    ``Conflict`` says why, and the item it was about is highlighted.
    ``Forbidden`` is only reachable by the owner typing a URL by hand;
    it gets one generic message whatever the item's state.
    """

    store = DjangoStore()
    try:
        saved = getattr(store, action)(request.person.id, item_id)
    except Conflict as refusal:
        try:
            item = store.shopper_item(request.person.id, item_id)
        except NotFound:
            raise Http404("Not found") from None
        owner = Item.objects.select_related("owner").get(pk=item_id).owner
        messages.warning(
            request,
            _CONFLICT_MESSAGES[refusal.reason].format(
                name=item.name, partner=owner.name
            ),
        )
        return redirect(_shop_location(store, request.person.id, owner.id, item_id))
    except (Forbidden, Invalid):
        messages.error(request, "Could not save that.")
        if len(store.partner_ids(request.person.id)) == 1:
            return redirect("their_list")
        return redirect("home")
    except NotFound:
        raise Http404("Not found") from None
    owner = Item.objects.select_related("owner").get(pk=saved.id).owner
    if action == "give" and owner.user_id is None:
        done = "Recorded {name} as given to {partner}."
    messages.success(request, done.format(name=saved.name, partner=owner.name))
    return redirect(_shop_location(store, request.person.id, owner.id, item_id))


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
    return _shopper_action(
        request,
        item_id,
        "give",
        "Gave {name} to {partner}. It's in {partner}'s Received list now.",
    )


@person_required
def add_item(request, list_key: str, owner_id: int | None = None):
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
        owner_id = _resolve_shop_owner(store, person.id, owner_id)
        partner = Person.objects.get(pk=owner_id)
        heading = f"Add to {partner.name}'s list"
        show_giver_note = True
        success_url = _shop_location(store, person.id, owner_id)
        cancel_url = success_url

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


@person_required
@require_POST
def record_gift_from(request, owner_id: int):
    """A gift the keeper got from someone who isn't on the app.

    It is an item on the keeper's own list, then the existing receive
    action with ``received_from`` set to that person's name.
    """

    store = DjangoStore()
    owner_id = _resolve_shop_owner(store, request.person.id, owner_id)
    person = Person.objects.get(pk=owner_id)
    if person.user_id is not None:
        raise Http404("Not found")
    back = _shop_location(store, request.person.id, owner_id)
    form = AddItemForm(request.POST, show_giver_note=False)
    if not form.is_valid():
        message = "Could not save that."
        if form.errors:
            message = next(iter(form.errors.values()))[0]
        messages.error(request, str(message))
        return redirect(back)
    try:
        with transaction.atomic():
            saved = store.add_item(
                request.person.id, request.person.id, **form.item_kwargs()
            )
            store.receive(request.person.id, saved.id, person.name)
    except (Invalid, Forbidden):
        messages.error(request, "Could not save that.")
        return redirect(back)
    except NotFound:
        raise Http404("Not found") from None
    messages.success(request, f"Saved {saved.name} as a gift from {person.name}.")
    return redirect(back)
