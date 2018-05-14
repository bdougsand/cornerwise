import pytz


class Region:
    def __init__(self, name: str, tz="US/Eastern"):
        self.name = name
        self.tz = pytz.timezone(tz)
        Region.regions[name.casefold()] = self

    @classmethod
    def get_regions(kls):
        return kls.regions.items()

    def localize_dt(self, dt):
        return self.tz.normalize(dt) if dt.tzinfo else self.tz.localize(dt)

    def local_now(self):
        return self.tz.normalize(pytz.utc.localize(datetime.utcnow()))

Region.regions = {}
somerville_ma = Region("Somerville, MA")
cambridge_ma = Region("Cambridge, MA")
