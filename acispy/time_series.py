class TimeSeriesData(object):
    def __init__(self, table):
        self.table = table

    def __getitem__(self, item):
        return self.table[item]

    def __contains__(self, item):
        return item in self.table

    def keys(self):
        return list(self.table.keys())

class EmptyTimeSeries(TimeSeriesData):
    def __init__(self):
        super(EmptyTimeSeries, self).__init__({})
