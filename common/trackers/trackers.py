# -*- coding: utf-8 -*-

from dataclasses import dataclass
from common.utility import ManageTitles
from . import tracker_list

@dataclass
class TRACKData:
    category: dict[str, int]
    freelech: dict[str, int]
    type_id: dict[str, int]
    resolution: dict[str, int]
    codec: list

    @classmethod
    def load_from_module(cls, tracker_name: str) -> "TRACKData":
        """
        Load tracker data from module
        """
        tracker_data= tracker_list[tracker_name.upper()]

        return cls(
            category=tracker_data.get("CATEGORY"),
            freelech=tracker_data.get("FREELECH"),
            type_id=tracker_data.get("TYPE_ID"),
            resolution=tracker_data.get("RESOLUTION"),
            codec=tracker_data.get("CODEC"),
        )

    # Types prioritaires qui doivent l'emporter sur les sources (bluray, bdrip…)
    # même si celles-ci apparaissent avant dans le nom de fichier.
    _PRIORITY_TYPES = frozenset({
        "remux", "bdremux", "full-disc", "vh", "untouched", "bd-untouched", "vu",
    })

    def filter_type(self, file_name: str) -> int:

        file_name = ManageTitles.clean(file_name)
        file_name = file_name.replace("-", " ")
        word_list = file_name.lower().strip().split(" ")

        # Passe 1 : types prioritaires (remux, full-disc…)
        for word in word_list:
            if word in self._PRIORITY_TYPES and word in self.type_id:
                return self.type_id[word]

        # Passe 2 : tous les autres types
        for word in word_list:
            if word in self.type_id:
                return self.type_id[word]

        # Passe 3 : fallback codec → encode
        for word in word_list:
            if word in self.codec:
                return self.type_id.get("encode", -1)
        return self.type_id.get("altro", -1)
