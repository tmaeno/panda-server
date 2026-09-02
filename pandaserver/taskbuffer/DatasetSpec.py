"""
dataset specification

"""

import datetime


class DatasetSpec(object):
    # attributes
    _attributes = (
        "vuid",
        "name",
        "version",
        "type",
        "status",
        "numberfiles",
        "currentfiles",
        "creationdate",
        "modificationdate",
        "MoverID",
        "transferStatus",
        "subType",
    )

    # Column types, taken from the Oracle schema of ATLAS_PANDA.DATASETS (panda-database
    # repo, schema/oracle). The columns are installed by __init__ via setattr, so a type
    # checker sees none of them without these declarations. They carry no value, so
    # nothing is created at class level.
    #
    # `| str` is not sloppiness. __getattribute__ below substitutes the string "NULL"
    # for a column that is still None, so a read genuinely yields either the column type
    # or that sentinel -- which is what makes `spec.numberfiles + 1` a latent TypeError.
    vuid: str
    name: str
    version: str
    type: str
    status: str
    numberfiles: int | str
    currentfiles: int | str
    creationdate: datetime.datetime | str
    modificationdate: datetime.datetime | str
    MoverID: int | str
    transferStatus: int | str
    subType: str

    # attributes which have 0 by default
    _zeroAttrs = ("MoverID", "transferStatus")

    # constructor
    def __init__(self):
        # install attributes
        for attr in self._attributes:
            setattr(self, attr, None)

    # override __getattribute__ for SQL
    def __getattribute__(self, name):
        ret = object.__getattribute__(self, name)
        if ret is None:
            return "NULL"
        return ret

    # return a tuple of values
    def values(self):
        ret = []
        for attr in self._attributes:
            val = getattr(self, attr)
            ret.append(val)
        return tuple(ret)

    # return map of values
    def valuesMap(self):
        ret = {}
        for attr in self._attributes:
            val = getattr(self, attr)
            if val == "NULL":
                if attr in self._zeroAttrs:
                    val = 0
                else:
                    val = None
            ret[f":{attr}"] = val
        return ret

    # pack tuple into DatasetSpec
    def pack(self, values):
        for i in range(len(self._attributes)):
            attr = self._attributes[i]
            val = values[i]
            setattr(self, attr, val)

    # return column names for INSERT
    @classmethod
    def columnNames(cls):
        ret = ""
        for attr in cls._attributes:
            if ret != "":
                ret += ","
            ret += attr
        return ret

    # return expression of values for INSERT
    @classmethod
    def valuesExpression(cls):
        ret = "VALUES("
        for attr in cls._attributes:
            ret += "%s"
            if attr != cls._attributes[len(cls._attributes) - 1]:
                ret += ","
        ret += ")"
        return ret

    # return expression of bind values for INSERT
    @classmethod
    def bindValuesExpression(cls):
        ret = "VALUES("
        for attr in cls._attributes:
            ret += f":{attr},"
        ret = ret[:-1]
        ret += ")"
        return ret

    # return an expression for UPDATE
    @classmethod
    def updateExpression(cls):
        ret = ""
        for attr in cls._attributes:
            ret = ret + attr + "=%s"
            if attr != cls._attributes[len(cls._attributes) - 1]:
                ret += ","
        return ret

    # return an expression of bind variables for UPDATE
    @classmethod
    def bindUpdateExpression(cls):
        ret = ""
        for attr in cls._attributes:
            ret += f"{attr}=:{attr},"
        ret = ret[:-1]
        return ret

    # return state values to be pickled
    def __getstate__(self):
        state = []
        for attr in self._attributes:
            val = getattr(self, attr)
            state.append(val)
        return state

    # restore state from the unpickled state values
    def __setstate__(self, state):
        for i, attr in enumerate(self._attributes):
            if i < len(state):
                setattr(self, attr, state[i])
            else:
                setattr(self, attr, None)
