"""Cornerwise admin configuration

Allow administrators to configure proposal, case, and project importers.

"""
from datetime import datetime, timedelta
from functools import reduce

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.contrib.gis.admin import GeoModelAdmin

from proposal.models import Event, Importer, Layer

from utils import geometry_from_url

import jsonschema


class CornerwiseAdmin(admin.AdminSite):
    site_header = "Cornerwise"


class ImporterForm(forms.ModelForm):
    timezone = forms.ChoiceField(choices=[("US/Eastern", "Eastern"),
                                          ("US/Central", "Central"),
                                          ("US/Mountain", "Mountain"),
                                          ("US/Pacific", "Pacific")])
    class Meta:
        model = Importer
        exclude = ["last_run"]


def run_importers(_modeladmin, request, importers):
    from proposal import tasks

    for importer in importers:
        found = len(tasks.fetch_proposals(None, importers=[importer]))
        messages.info(
            request, f"Found {found} new proposal(s) using {importer}")


def validate_importers(_, request, importers):
    when = datetime.now() - timedelta(days=30)
    for importer in importers:
        data = importer.updated_since(when)
        try:
            importer.validate(data)
        except jsonschema.exceptions.ValidationError as err:
            schema_path = "/".join(map(str, err.absolute_schema_path))
            messages.warning(
                request,
                f"Validation error for {importer} "
                f"at {schema_path}: {err.message}")
            messages.warning(
                request,
                str(reduce(lambda d, k: d[k], err.absolute_path, data)),
            )
        else:
            messages.info(request, f"{importer} successfully validated!")


class ImporterAdmin(admin.ModelAdmin):
    model = Importer
    form = ImporterForm
    actions = [run_importers, validate_importers]


class LayerForm(forms.ModelForm):
    auto_calculate_envelope = forms.BooleanField(
        initial=True,
        help_text="""If True, the geometry envelope will be calculated
        automatically from the contents of the GeoJSON.
        """)

    class Meta:
        model = Layer
        fields = "__all__"

    def save(self, commit=True):
        m = super().save(commit=False)

        if self.fields["auto_calculate_envelope"]:
            gc = geometry_from_url(self.fields["url"])
            # TODO Add a buffer to the envelope? (gc.envelope.buffer)
            m.envelope = gc.envelope

        if commit:
            m.save()
        return m


class LayerAdmin(GeoModelAdmin):
    default_lat = 42.387545768736246
    default_lon = -71.14565849304199

    form = LayerForm


cornerwise_admin = CornerwiseAdmin(name="admin")

cornerwise_admin.register(Importer, ImporterAdmin)
cornerwise_admin.register(Layer, LayerAdmin)
cornerwise_admin.register(User, UserAdmin)
cornerwise_admin.register(Event, admin.ModelAdmin)
