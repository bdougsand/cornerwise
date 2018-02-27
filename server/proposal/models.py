from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.gis.geos import Point
from django.core.validators import MinValueValidator
from django.core.urlresolvers import reverse
from django.dispatch import receiver
from django.db import IntegrityError
from django.db.models import Q
from django.forms.models import model_to_dict
from django.utils import dateparse

import json
import pickle
from urllib import request
import utils

import jsonschema
import pytz


class ProposalManager(models.GeoManager):
    def latest(self):
        results = self.order_by("-created")
        return results and results[0]

    def between(self, start=None, end=None):
        q = None

        if start:
            q = Q(created__gte=start)

        if end:
            endQ = Q(closed_lte=end)

            if q:
                q = Q & endQ
            else:
                q = endQ

        return self.filter(q)

    def for_parcel(self, parcel):
        return self.filter(location__within=parcel.shape)


UNSET = object()
def make_property_map():
    def _g(p, default=UNSET):
        return lambda d: d.get(p, default)

    def _G(p):
        return lambda d: d[p]

    def get_other_addresses(d):
        return ";".join(d["all_addresses"][1:]) if "all_addresses" in d else ""


    return [("address", lambda d: d["all_addresses"][0]),
            ("other_addresses", get_other_addresses),
            ("location", lambda d: Point(d["location"]["long"], d["location"]["lat"])),
            ("summary", lambda d: d["summary"][0:1024] if "summary" in d else UNSET),
            ("description", _g("description")),
            ("source", _g("source")),
            ("region_name", _g("region_name")),
            ("updated", utils.make_fn_chain(_G("updated_date"), dateparse.parse_datetime)),
            ("complete", _G("complete")),
            ("status", _g("status"))]


property_map = make_property_map()


class Proposal(models.Model):
    case_number = models.CharField(
        max_length=64,
        unique=True,
        help_text=("The unique case number "
                   "assigned by the city"))
    address = models.CharField(max_length=128, help_text="Street address")
    other_addresses = models.CharField(
        max_length=250,
        blank=True,
        help_text="Other addresses covered by this proposal")
    location = models.PointField(help_text="The latitude and longitude")
    region_name = models.CharField(
        max_length=128, default="Somerville, MA", null=True, help_text="")
    # The time when the proposal was last saved:
    modified = models.DateTimeField(auto_now=True)
    # The last time that the source was changed:
    updated = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)
    summary = models.CharField(max_length=1024, default="")
    description = models.TextField(default="")
    source = models.URLField(
        null=True, help_text="The data source for the proposal.")
    status = models.CharField(max_length=64)

    # A proposal can be associated with a Project:
    project = models.ForeignKey("project.Project", blank=True, null=True)
    # A misnomer; if True, indicates that the planning board has issued a
    # ruling (approval or disapproval). Does not actually reflect whether the
    # proposed changes are done.
    complete = models.BooleanField(default=False)
    importer = models.ForeignKey("proposal.Importer", blank=True,
                                 null=True, on_delete=models.SET_NULL)

    parcel = models.ForeignKey(
        "parcel.Parcel", related_name="proposals", null=True, on_delete=models.SET_NULL)

    objects = ProposalManager()

    @classmethod
    def create_or_update_from_dict(cls, p_dict: dict):
        """
        Constructs a Proposal from a dictionary.  If an existing proposal has a
        matching case number, update it from p_dict.

        :param cls:
        :param p_dict: dictionary describing a proposal"""

        proposal: Proposal
        try:
            proposal = cls.objects.get(case_number=p_dict["case_number"])
            created = False
        except cls.DoesNotExist:
            proposal = cls(case_number=p_dict["case_number"])
            created = True

        proposal.update_from_dict(p_dict)

        return (created, proposal)


    @property
    def attribute_dict(self):
        return dict(self.attributes.values_list("name", "text_value"))

    def get_absolute_url(self):
        return reverse("view-proposal", kwargs={"pk": self.pk})

    def document_for_field(self, field):
        return self.documents.filter(field=field)

    def update_from_dict(self, p_dict):
        changed = bool(self.pk)

        if changed:
            prop_changes = []

        for prop, fn in property_map:
            old_val = changed and getattr(self, prop)
            try:
                val = fn(p_dict)
                if val is not UNSET:
                    if changed:
                        prop_changes.append({
                            "name": prop,
                            "new": val,
                            "old": old_val
                        })
                    setattr(self, prop, val)
            except KeyError as exc:
                # The getters should throw a KeyError whenever a required
                # property is missing.

                # Don't raise an error if the property was already set.
                if old_val is not None:
                    continue
                raise Exception("Missing required property: %s\n Reason: %s" %
                                (prop, exc))

        self.save()

        # Add related events:
        for event in self.create_events(p_dict.get("events", [])):
            event.proposals.add(self)

        # Create associated documents:
        document_fields = ["decisions", "reports", "other"]
        doc_dicts = zip(document_fields, map(p_dict.get, document_fields))
        self.create_documents((field, val["links"])
                              for field, val in doc_dicts
                              if val and val.get("links"))

        updated = p_dict.get("updated_date", self.updated)
        if changed:
            attr_changes = []
        for attr_name, attr_val in p_dict.get("attributes", []):
            try:
                handle = utils.normalize(attr_name)
                attr = self.attributes.get(handle=handle)
                old_val = attr.text_value
            except Attribute.DoesNotExist:
                self.attributes.create(
                    name=attr_name,
                    handle=handle,
                    text_value=attr_val,
                    published=updated)
                old_val = None
            if changed:
                attr_changes.append({
                    "name": attr_name,
                    "old": old_val,
                    "new": attr_val
                })

        if changed:
            changeset = Changeset.from_changes(self, {
                "properties": [ch for ch in prop_changes if ch["old"] != ch["new"]],
                "attributes": [ch for ch in attr_changes if ch["old"] != ch["new"]]
            })
            changeset.save()

    def create_documents(self, docs):
        for field, links in docs:
            for link in links:
                try:
                    doc = self.documents.get(url=link["url"])
                except Document.DoesNotExist:
                    self.documents.create(url=link["url"],
                                          title=link["title"],
                                          field=field,
                                          published=self.updated)

    def create_events(self, event_dicts):
        return list(map(Event.make_event, event_dicts)) if event_dicts else []


class Attribute(models.Model):
    """
    Arbitrary attributes associated with a particular proposal.
    """
    proposal = models.ForeignKey(Proposal, related_name="attributes")
    name = models.CharField(max_length=128)
    handle = models.CharField(max_length=128, db_index=True)

    # Either the date when the source document was published or the date
    # when the attribute was observed:
    published = models.DateTimeField()
    text_value = models.TextField(null=True)
    date_value = models.DateTimeField(null=True)

    # class Meta:
    #     unique_together = ("proposal", "handle")

    def to_dict(self):
        d = {"name": self.name, "handle": self.handle}
        if self.text_value:
            d["value"] = self.text_value
            d["value_type"] = "text"
        elif self.date_value:
            d["value"] = self.date_value.isoformat()
            d["value_type"] = "date"

        return d

    def set_value(self, v):
        if isinstance(v, str):
            self.text_value = v
        elif isinstance(v, datetime):
            self.date_value = v

    def clear_value(self):
        self.text_value = None
        self.date_value = None

    @property
    def value(self):
        return self.text_value or \
            self.date_value


class EventManager(models.Manager):
    def upcoming(self):
        today = pytz.UTC.localize(datetime.today())
        return self.filter(date__gte=today).order_by("date")


class Event(models.Model):
    """
    Meeting or hearing associated with a proposal.
    """
    title = models.CharField(max_length=256, db_index=True)
    created = models.DateTimeField(auto_now_add=True)
    date = models.DateTimeField(db_index=True)
    duration = models.DurationField(null=True)
    location = models.CharField(
        max_length=256, default="Somerville City Hall, 93 Highland Ave")
    region_name = models.CharField(max_length=128, default="Somerville, MA")
    description = models.TextField()
    proposals = models.ManyToManyField(
        Proposal, related_name="events", related_query_name="event")
    minutes = models.URLField(blank=True)

    objects = EventManager()

    class Meta:
        unique_together = (("date", "title", "region_name"))

    def __str__(self):
        datestr = self.date.strftime("%b %d, %Y")
        return f"{self.title}, {datestr}, {self.region_name}"

    def to_json_dict(self):
        d = model_to_dict(self, exclude=["created", "proposals"])
        return d

    @classmethod
    def make_event(cls, event_dict):
        """
        event_dict should have the following fields:
        - title (str) - Name of the event
        - description (str)
        - date (datetime with local timezone) - When will the event occur?
        - cases - A list of case numbers
        - region_name
        - duration (timedelta, optional) - how long will the event last?
        - agenda_url (string, optional)
        """
        start = dateparse.parse_datetime(event_dict["start"])
        kwargs = {"title": event_dict["title"],
                  "date": start,
                  "region_name": event_dict["region_name"]}
        try:
            event = cls.objects.get(**kwargs)
        except cls.DoesNotExist:
            kwargs["minutes"] = event_dict.get("agenda_url", "")
            event = cls(**kwargs)

        event.date = start
        event.minutes = event_dict.get("agenda_url", "")
        event.duration = utils.fn_chain(event_dict, "duration", utils.parse_duration)

        event.save()

        for case_number in event_dict.get("cases", []):
            try:
                proposal = Proposal.objects.get(case_number=case_number)
                event.proposals.add(proposal)
            except Proposal.DoesNotExist:
                continue

        return event


def upload_document_to(doc, filename):
    return "doc/%s/%s" % (doc.pk, filename)


class Document(models.Model):
    """
    A document associated with a proposal.
    """
    proposal = models.ForeignKey(Proposal, related_name="documents")
    event = models.ForeignKey(
        Event, null=True, help_text="Event associated with this document")
    url = models.URLField()
    title = models.CharField(
        max_length=256, help_text="The name of the document")
    field = models.CharField(
        max_length=256,
        help_text=("The field in which the document was found"))
    # Record when the document was first observed:
    created = models.DateTimeField(auto_now_add=True)

    # If available: when the document was published.
    published = models.DateTimeField(null=True)

    # If the document has been copied to the local filesystem:
    document = models.FileField(null=True, upload_to=upload_document_to)

    # File containing extracted text of the document:
    fulltext = models.FileField(null=True)
    encoding = models.CharField(max_length=20, default="")
    # File containing a thumbnail of the document:
    thumbnail = models.FileField(null=True, upload_to=upload_document_to)

    class Meta:
        # Ensure at the DB level that documents are not duplicated:
        unique_together = (("proposal", "url"))

    def get_absolute_url(self):
        return reverse("view-document", kwargs={"pk": self.pk})

    def to_dict(self):
        d = model_to_dict(
            self, exclude=["event", "document", "fulltext", "thumbnail"])
        if self.thumbnail:
            d["thumb"] = self.thumbnail.url

        if self.document:
            d["url"] = self.document.url

        return d

    def get_text(self):
        with open(self.fulltext.path, "r", encoding=self.encoding) as f:
            return f.read()

    @property
    def line_iterator(self):
        return (line.decode(self.encoding) for line in self.fulltext)

    @property
    def local_path(self):
        return self.document and self.document.path or ""


@receiver(models.signals.post_delete, sender=Document)
def auto_delete_document(**kwargs):
    """Signal to clean up the files associated with a document when it is deleted
    from the database.

    """
    document = kwargs["instance"]
    if document.document:
        document.document.delete(save=False)
    if document.thumbnail:
        document.thumbnail.delete(save=False)
    if document.fulltext:
        document.fulltext.delete(save=False)


def upload_image_to(doc, filename):
    fmt = "doc/%s/images/%s" if doc.document else "prop/%s/%s"
    return fmt % (doc.document_id, filename)


class Image(models.Model):
    """
    An image associated with a proposal and (optionally) with a document.
    """
    proposal = models.ForeignKey(Proposal, related_name="images")
    document = models.ForeignKey(
        Document, null=True, help_text="Source document for image",
        related_name="images")
    image = models.FileField(null=True, upload_to=upload_image_to)
    width = models.IntegerField()
    height = models.IntegerField()
    thumbnail = models.FileField(null=True)
    url = models.URLField(null=True, unique=True, max_length=512)
    # Crude way to specify that an image should not be copied to the
    # local filesystem:
    skip_cache = models.BooleanField(default=False)
    # Crude form of ranking. Images with lower priority values are shown first
    # in the UI.
    priority = models.IntegerField(default=0, db_index=True)
    source = models.CharField(max_length=64, default="document")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("proposal", "image"))

    def get_url(self):
        return self.image and self.image.url or self.url

    def to_dict(self):
        return {
            "id": self.pk,
            "src": self.get_url(),
            "thumb": self.thumbnail.url if self.thumbnail else None
        }


@receiver(models.signals.post_delete, sender=Image)
def auto_delete_image(**kwargs):
    image = kwargs["instance"]
    if image.image:
        image.image.delete(save=False)
    if image.thumbnail:
        image.thumbnail.delete(save=False)


class Changeset(models.Model):
    """
    Model used to record the changes to a Proposal over time.
    """
    proposal = models.ForeignKey(Proposal, related_name="changesets")
    created = models.DateTimeField(auto_now_add=True)
    change_blob = models.BinaryField()

    @classmethod
    def from_changes(kls, proposal, changes):
        """Create a new Changeset for a proposal.

        :param proposal: a Proposal object

        :param changes: a dictionary with "properties" and "attributes" keys.
        Each should be a list of dicts containing "name", the name of the
        change property or attribute; "new", the new value or None; and "old",
        the old value or None.

        """
        instance = kls(proposal=proposal)
        instance.changes = changes
        return instance

    @property
    def changes(self):
        # { "properties": [ { } ] ,
        #   "attributes": [ { } ] }
        d = getattr(self, "_change_dict", None)
        if not d:
            d = pickle.loads(self.change_blob) if self.change_blob else {}
            self._change_dict = d
        return d

    @changes.setter
    def changes(self, d):
        self._change_dict = d
        self.change_blob = pickle.dumps(d)


@utils.lazy
def get_importer_schema():
    with request.urlopen(settings.IMPORTER_SCHEMA) as u_in:
        return json.loads(u_in.read())


class Importer(models.Model):
    """Importers are created through the administrator interface. Cornerwise
    will place a GET request to the importer's URL once a day with a `when`
    query parameter. The endpoint should return a result conforming to the
    format documented in `docs/scraper-schema.json`.
    """
    name = models.CharField(max_length=128,
                            unique=True,
                            help_text="""Readable name that will be used
                            to identify the origin of the proposals. """)
    region_name = models.CharField(max_length=128,
                                   blank=True,
                                   help_text="""Default region name, used when
                                   a proposal in the response JSON does not
                                   have one set.""")
    timezone = models.CharField(default="US/Eastern", max_length=25,
                                help_text="""Ambiguous date fields will be
                                interpreted as having this timezone""")
    url = models.URLField(help_text="""A URL endpoint that should accept a
    `when` parameter of the format YYYYmmdd and should respond with a JSON
    document conforming to the scraper-schema spec.""")
    run_frequency = models.DurationField(
        help_text="""Specifies how often the scraper should run. Effectively
    rounds up to the next day, so values of '3 days' and '2 days, 3 hours' are
    the same.""",
        default=timedelta(days=1),
        validators=[MinValueValidator(timedelta(days=1))])
    last_run = models.DateTimeField(help_text="Last time the scraper ran",
                                    null=True)

    def __str__(self):
        return self.name

    @property
    def timezone(self):
        return pytz.timezone(self.timezone)

    def url_for(self, when=None):
        params = when and {"when": when.strftime("%Y%m%d")}
        return utils.add_params(self.url, params)

    def updated_since(self, when):
        with request.urlopen(self.url_for(when)) as u:
            return json.load(u)

    def validate(self, data, schema=None):
        return jsonschema.validate(data, schema or get_importer_schema())

    def cases_since(self, when):
        return self.updated_since(when).get("cases")


class Layer(models.Model):
    name = models.CharField(max_length=100,
                            help_text="Name shown to users")
    short_name = models.CharField(max_length=30, blank=True)
    description = models.TextField(help_text="""A detailed description of the layer""")
    icon_text = models.CharField(max_length=10,
                                 help_text="""Emoji or short string shown as
                                 shorthand for the layer if there is no icon set.""")
    icon = models.FileField(default=None, null=True,)
    icon_credit = models.CharField(max_length=100,
                                   blank=True,
                                   help_text="""Many popular icons require that
                                   proper attribution be given to the original
                                   author(s). Use this field to provide it.""")
    region_name = models.CharField(max_length=128,
                                   blank=True,
                                   help_text="""Used when filtering layers by
                                   region name""")
    url = models.URLField(
        help_text="""URL of the GeoJSON representing the
                  layer geometry.
        """)
    envelope = models.PolygonField(srid=4326,
                                   null=True,
                                   help_text="""Polygon for the bounding envelope
                                   of the layer geometry. Used to determine
                                   which layers should be loaded when looking
                                   at the map. Calculated automatically from
                                   the geometry in the layer""")

    def calculate_envelope(self):
        gc = utils.geometry_from_url(self.url)
        self.envelope = gc.envelope
