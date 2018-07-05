from collections import defaultdict, OrderedDict

from django.forms.models import model_to_dict
from django.utils import timezone

from proposal.models import Changeset, Document, Image, Proposal, Event
from proposal.views import proposal_json
from proposal.query import build_proposal_query_dict


def summarize_query_updates(query_dict, since=None, until=None):
    """Takes a proposal query dict and a range of dates. Constructs a dictionary
    summarizing the changes that are relevant to that query.

    Returns a dictionary with the following structure:

    - "changes": an ordered dict mapping proposal ids to dicts describing the
      new or changed properties of the proposal

    - "new": count of new proposals

    - "updated": count of updated proposals

    - "total": new + updated count

    - "start"/"end": numeric timestamps indicating start and end

    """
    if since:
        query_dict["created__gt"] = since
    # Find proposals that are NEW since the given date:
    proposals = Proposal.objects.filter(**query_dict)
    new_ids = {proposal.pk for proposal in proposals}

    # Find proposals that have *changed*, but which are not new:
    proposals_changed = Proposal.objects\
                                .exclude(pk__in=new_ids)\
                                .filter(updated__gt=since, **query_dict)
    if until:
        proposals_changed = proposals_changed.filter(updated__lte=until)

    # Start with the new proposals:
    summary = OrderedDict((p.id, {
        "proposal": proposal_json(
            p, include_images=1, include_documents=False),
        "new": True
    }) for p in proposals)

    if proposals_changed:
        # Only include changesets for proposals we're interested in:
        ids = [proposal.pk for proposal in proposals_changed]
        changes = Changeset.objects.filter(created__gt=since,
                                           proposal__in=ids)\
                                   .order_by("created")

        prop_changes = defaultdict(list)
        attr_changes = defaultdict(list)
        for change in changes:
            change_dict = change.changes
            pchange_list = prop_changes[change.proposal_id]
            achange_list = attr_changes[change.proposal_id]
            pchange_list.extend(change_dict["properties"])
            achange_list.extend(change_dict["attributes"])

        for p in proposals_changed:
            summary[p.id] = {
                "proposal": proposal_json(
                    p, include_images=1, include_documents=False),
                "new": False,
                "properties": prop_changes[p.id],
                "attributes": attr_changes[p.id],
                "documents": [],
                "images": []
            }

    # Find new Documents:
    documents = Document.objects.filter(
        proposal__in=proposals_changed, created__gt=since)
    if until:
        documents = documents.filter(updated__lte=until)

    for doc in documents:
        if doc.proposal_id in summary:
            summary[doc.proposal_id]["documents"].append(doc.to_dict())

    # Find new Images:
    images = Image.objects.filter(
        proposal__in=proposals_changed, created__gt=since)
    if until:
        images = images.filter(updated__lte=until)

    for image in images:
        if image.proposal_id in summary:
            summary[image.proposal_id]["images"].append(image.to_dict())

    return {"changes": summary,
            "new": len(new_ids),
            "updated": len(proposals_changed),
            "total": len(new_ids) + len(proposals_changed),
            "start": since.timestamp(),
            "end": until.timestamp() if until else None}


def summarize_subscription_updates(subscription, since, until=None):
    """Generates a dictionary describing the updates relevant to the given
    Subscription that occurred after `since` through `until` (if given).

    :subscription: a subscription with a `query_dict` property containing a
    dictionary suitable for building a proposal query.
    :since: a datetime
    :until: datetime

    :returns: a dictionary with the keys "changes", containing a dictionary
    mapping proposal ids to changes; "new", a count of the new proposals; and
    "updated", a count of the updated proposals.

    """
    query_dict = subscription.query_dict
    if query_dict is None:
        return None

    query = build_proposal_query_dict(query_dict)
    # Don't include changes that predate the Subscription:
    since = max(subscription.created, since)
    return summarize_query_updates(query, since, until)


def combine_change_updates(summaries):
    """If a user has multiple subscriptions, combine them into a single update
    summary dict. If a proposal is new for one Subscription but updated for
    another, just show the updated fields.

    """
    if len(summaries) == 1:
        return summaries[0]

    combined_changed = OrderedDict()
    combined_new = OrderedDict()
    new_count = 0
    changed_count = 0
    end = start = timezone.now()

    for summary in summaries:
        sub = None
        if isinstance(summary, tuple):
            sub, summary = summary

        start = min(start, summary["start"])
        end = min(end, summary["end"])

        for pk, proposal in summary["changes"].items():
            if pk in combined_new:
                if proposal["new"]:
                    if sub:
                        combined_new[pk]["subscriptions"].append(sub)
                else:
                    other_subs = combined_new[pk]["subscriptions"]
                    if sub:
                        other_subs.append(sub)
                    proposal["subscriptions"] = other_subs
                    combined_changed[pk] = proposal
                    del combined_new[pk]
            elif pk in combined_changed:
                if sub:
                    combined_changed[pk]["subscriptions"].append(sub)
            else:
                proposal["subscriptions"] = [sub] if sub else []
                if proposal["new"]:
                    combined_new[pk] = proposal
                else:
                    combined_changed[pk] = proposal

        new_count = len(combined_new)
        changed_count = len(combined_changed)
        combined_new.update(combined_changed)

        return {
            "changes": combined_new,
            "new": new_count,
            "updated": new_count + changed_count,
            "total": new_count + changed_count,
            "start": start,
            "end": end,
        }


def summarize_event(event):
    d = model_to_dict(event, exclude=["created", "proposals"])
    d["proposals"] = [{"case_number": p.case_number,
                       "address": p.address} for p in event.proposals]
    return d


def summarize_event_updates(since, until=None, region=None):
    query = {"created__gte": since}
    if until:
        query["created_lt"] = until
    if region:
        query["region_name"] = region

    events = Event.objects.filter(**query)

    if events:
        return {"events": [{"title": event.title,
                            "date": event.date} for event in events]}


def find_updates(subscriptions, since, until=None):
    """Find and summarize changes that are relevant to the given subscriptions.

    Note that this is not very scalable, but frankly, I don't think it needs to
    be. There are operations-level strategies for scaling that wouldn't require
    too much to be changed here.

    :subscription: A collection of Subscription objects
    :since: datetime
    :until: datetime

    """
    for subscription in subscriptions:
        summary = summarize_subscription_updates(subscription, since, until)

    return summary

