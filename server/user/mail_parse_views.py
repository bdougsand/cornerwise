from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

import logging

import site_config
from shared import mail
from . import tasks

logger = logging.getLogger(__name__)

User = get_user_model()


def send_fowarded_message(users, from_email, text, to_username="admin"):
    emails = [user.email for user in users]
    mail.send(emails, "Cornerwise: Received Message",
              "forwarded", {"text": text,
                            "from": from_email,
                            "to_username": to_username})


def forward_mail(username, from_email, text):
    try:
        if username == "admin":
            users = User.objects.filter(is_staff=True)
        else:
            config = site_config.by_name(username)
            if config and config.group_name:
                users = User.objects.filter(groups__name=config.group_name)\
                                    .exclude(email="")
            else:
                users = [User.objects.get(username=username, is_staff=True)]
        send_fowarded_message(users, from_email, text, username)
        return HttpResponse(f"OK: Forwarded email to {len(users)} user(s)", status=200)
    except User.DoesNotExist:
        logger.warn(f"Received email for unknown user '{username}' from {from_email}")
        return HttpResponse("OK: Not sent", status=200)


# When email is sent to @cornerwise.org, SendGrid parses it and posts the
# reults to this view:
@csrf_exempt
def mail_inbound(request):
    """
    See https://sendgrid.com/docs/API_Reference/Webhooks/parse.html for
    documentation of the POST fields.
    """
    try:
        parse_key = settings.SENDGRID_PARSE_KEY
        if parse_key and request.GET.get("key", "") != parse_key:
            logger.warning("Received mail_inbound request with a bad key")
            return HttpResponse("NOK: bad key", status=403)

        from_email = request.POST["from"]
        body = request.POST["text"]

        (username, hostname) = request.POST["to"].lower().split("@", 1)
        if username not in {"cornerwise", "noreply"}:
            return forward_mail(username, from_email, body)

        user = User.objects.get(email=from_email)
        lines = request.POST["text"].split("\n")
        for line in lines:
            command = line.strip().split(" ")[0]
            if command in email_commands:
                email_commands(request, user)
                logger.info(f"Successfully processed email from {from_email}")
                return HttpResponse("OK", status=200)

        logger.warning(f"Email from {from_email} contained no recognized commands")

        return HttpResponse("NOK: no commands recognized", status=200)
    except KeyError as kerr:
        logger.exception(f"Missing required key in POST data")
    except User.DoesNotExist:
        logger.warning("Received an email from unknown user %s", request.POST["from"])

    # SendGrid will retry if there's a status code other than 200.
    return HttpResponse("NOK: invalid message", status=200)


def deactivate_account(request, user):
    user.profile.deactivate()
    tasks.send_deactivation_email.delay(user)


def reset_account(request, user):
    user.profile.generate_token()
    tasks.send_reset_email.delay(user)


email_commands = {"deactivate": deactivate_account, "reset": reset_account}
