from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

_HTTPS_ONLY = URLValidator(schemes=["https"])
_URL_ERROR = "Use a link that starts with https://"


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"autofocus": True, "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update(
            {"autocomplete": "current-password"}
        )


class AddItemForm(forms.Form):
    """Add-item fields. List identity is the URL, not a picker on this form."""

    name = forms.CharField(
        max_length=200,
        label="Name",
        error_messages={"required": "Name is required"},
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "off",
                "placeholder": "What is it?",
            }
        ),
    )
    url = forms.CharField(
        max_length=500,
        required=False,
        label="Link",
        widget=forms.TextInput(attrs={"placeholder": "https://…"}),
    )
    price_text = forms.CharField(
        max_length=100,
        required=False,
        label="Price",
        widget=forms.TextInput(attrs={"placeholder": "around $120"}),
    )
    tag = forms.CharField(
        max_length=100,
        required=False,
        label="Tag",
        widget=forms.TextInput(attrs={"placeholder": "Birthday"}),
    )
    giver_note = forms.CharField(
        required=False,
        label="Private note",
        help_text="Only you will see this.",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "size, color, where to hide it"}),
    )

    def __init__(self, *args, show_giver_note: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.show_giver_note = show_giver_note
        if not show_giver_note:
            self.fields.pop("giver_note")

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required")
        return name

    def clean_url(self) -> str:
        """HTTPS only: the link lands in an ``href`` the other person clicks.

        A bare ``example.com/coat`` gets ``https://`` in front. Any other
        scheme (``http:``, ``javascript:``, ``data:``) is rejected.
        """

        url = (self.cleaned_data.get("url") or "").strip()
        if not url:
            return ""
        if "://" not in url:
            url = f"https://{url}"
        if len(url) > 500:
            raise forms.ValidationError("Link is too long")
        try:
            _HTTPS_ONLY(url)
        except ValidationError:
            raise forms.ValidationError(_URL_ERROR) from None
        return url

    def _optional(self, key: str) -> str | None:
        value = (self.cleaned_data.get(key) or "").strip()
        return value or None

    def item_kwargs(self) -> dict:
        kwargs = {
            "name": self.cleaned_data["name"],
            "url": self._optional("url"),
            "price_text": self._optional("price_text"),
            "tag": self._optional("tag"),
        }
        if self.show_giver_note:
            kwargs["giver_note"] = self._optional("giver_note")
        return kwargs
