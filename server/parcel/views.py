from django.contrib.gis.geos import Point
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from functools import reduce
import json, operator, re

from shared.address import normalize_number, normalize_street, split_address
from shared.request import make_response, ErrorResponse

from .models import Parcel, Attribute


def make_query(d, reducer=operator.and_):
    subqueries = []

    if "id" in d:
        return Q(pk=d["id"])

    if "lng" in d and "lat" in d:
        try:
            lat = float(d["lat"])
            lng = float(d["lng"])
            subqueries.append(Q(shape__contains=Point(lng, lat, srid=4326)))
        except ValueError as err:
            raise ErrorResponse(
                "Bad parameter",
                {"error": "Bad value for latitude or longitude."},
                err=err)

    if "loc_id" in d:
        subqueries.append(Q(loc_id=d["loc_id"]))

    street = d.get("street")
    number = d.get("number")
    if not street and "address" in d:
        m = split_address(d["address"])
        if m:
            number, street = m

    if street:
        sq = {"full_street": normalize_street(street)}

        if number:
            sq["address_num"] = normalize_number(number)

        subqueries.append(Q(**sq))

    if d.get("mode") == "or":
        reducer = operator.or_

    q = reduce(reducer, subqueries)

    if "types" in d:
        types = [t.upper() for t in re.split(r"\s*,\s*", d["types"])]
        return q & Q(poly_type__in=types)
    elif "include_row" not in d:
        return q & ~Q(poly_type="ROW")


def geojson_data(geom):
    return {
        "type": geom.__class__.__name__,
        "coordinates": geom.coords
    }


def parcels_for_request(req):
    """
    Helper function for retrieving the parcel(s) at a given latitude and
    longitude.
    """
    query = make_query(req.GET)
    queryset = Parcel.objects.filter(query)

    # Include attributes
    if req.GET.get("attributes"):
        queryset = queryset.prefetch_related("attributes")

    if not req.GET.get("multi"):
        queryset = queryset[0:1]

    return queryset


def parcels_for_multi_request(req):
    """
    Find parcels using the "points" parameter of a GET request. The value
    should be a JSON-encoded array of dictionaries with "lat", "lng", and "id"
    values. UNUSED.
    """
    try:
        print(req.GET["points"])
        points = json.loads(req.GET["points"])
    except KeyError as kerr:
        raise ErrorResponse(
            "", {"error": "Missing required key: {}".format(kerr)})
    except ValueError:
        raise ErrorResponse("", {
            "error":
            "Malformed 'points' value; must be valid JSON array: {}".format(e)
        })

    query = None
    for point in points:
        q = Q(shape__contains=Point(point["lng"], point["lat"], srid=4326))
        if query:
            query |= q
        else:
            query = qe

    return Parcel.objects.filter(query)


def make_parcel_data(parcel, include_attributes=False):
    d = geojson_data(parcel.shape)
    d["properties"] = {"type": parcel.poly_type,
                       "loc_id": parcel.loc_id,
                       "id": parcel.pk}

    if include_attributes:
        d["properties"].update({
            a.name: a.value
            for a in parcel.attributes.all()
        })

    return d


@make_response()
def find_parcels(req):
    try:
        parcel = parcels_for_request(req)[0]

        include_attributes = req.GET.get("attributes", False)
    except IndexError:
        raise ErrorResponse("No matching parcels found", status=404)
    return make_parcel_data(parcel, include_attributes=include_attributes)


@make_response()
def view_parcel(req, pk):
    parcel = get_object_or_404(Parcel, pk=pk)


@make_response()
def parcel_with_loc_id(req, loc_id=None):
    if not loc_id:
        loc_id = req.GET["loc_id"]

    parcel = Parcel.objects.get(loc_id=loc_id)
    return make_parcel_data(parcel, include_attributes=True)
