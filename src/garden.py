import builtins
import json
from typing import List, Dict, cast, TypedDict, TYPE_CHECKING, Union

import jsonschema  # type: ignore
from jsonschema_typed import JSONSchema  # type: ignore

GardenSchema = JSONSchema["src/schemas/garden_schema.json"]
PlantSchema = JSONSchema["src/schemas/plant_schema.json"]

# this is the generated type of PlantSchema. Manually update this when the plant schema changes!!!!
CropBase: TypedDict = TypedDict('CropBase', {'name': builtins.str, 'companions': List[builtins.str],
                                             'greywater_ok': builtins.bool,
                                             'yield': Union[builtins.int, builtins.float],
                                             'water_use': Union[builtins.int, builtins.float],
                                             'delta_n': Union[builtins.int, builtins.float],
                                             'lifespan': Union[builtins.int, builtins.float],
                                             'is_cover_crop': builtins.bool})


class Crop(CropBase, total=False):
    is_compound: bool
    plant_1_yield: float
    plant_2_yield: float
    plant_1_name: str
    plant_2_name: str


class Garden:

    def __init__(self, garden_file: str, plant_files: List[str]):
        # load in the garden file and all of the plant files
        # 1. validate against schema
        # 2. generate compound crops
        self.crops: List[Crop] = []
        self.garden: GardenSchema

        # read in the garden file, validate against schema
        with open(garden_file, "r") as g, open("src/schemas/garden_schema.json", "r") as s:
            garden_schema = json.loads(s.read())
            self.garden = json.loads(g.read())
            self.garden["weeks"] = cast(int, self.garden["weeks"])
            jsonschema.validate(instance=self.garden, schema=garden_schema)

        # read in all of the plant files, validate them, store in crops
        for p_file in plant_files:
            with open("src/schemas/plant_schema.json", "r") as ps:
                plant_schema = json.loads(ps.read())
                with open(p_file, "r") as p:
                    plant: Crop = json.loads(p.read())
                    jsonschema.validate(instance=plant, schema=plant_schema)
                    plant['is_compound'] = False
                    self.crops.append(plant)

        # generate compound crops, add them to the crops list
        compound_crops: List[Crop] = []
        for crop in self.crops:
            companions: List[Crop] = [c for c in self.crops if c["name"] in crop["companions"]]
            for companion in companions:
                longest_lifespan = max(crop["lifespan"], companion["lifespan"])
                crop_multiplier = (longest_lifespan//crop["lifespan"])
                companion_multiplier = (longest_lifespan//companion["lifespan"])
                crop_yield =  crop_multiplier * crop["yield"]
                companion_yield = companion_multiplier * companion["yield"]
                compound_crops.append({
                    "name": f"{crop['name']}-{companion['name']}",
                    "plant_1_name": crop["name"],
                    "plant_2_name": companion['name'],
                    "companions": [],
                    "delta_n": crop["delta_n"] * crop_multiplier + companion["delta_n"] * companion_multiplier,
                    "water_use": crop["water_use"] + companion["water_use"],
                    "greywater_ok": crop["greywater_ok"] and companion["greywater_ok"],
                    "yield": crop_yield + companion_yield,
                    "plant_1_yield": crop_yield,
                    "plant_2_yield": companion_yield,
                    "is_compound": True,
                    "lifespan": longest_lifespan,
                    "is_cover_crop": crop["is_cover_crop"] and companion["is_cover_crop"]
                })
        self.crops.extend(compound_crops)

    def get_target_yields(self, target: str) -> Dict[str, float]:
        """
        :param target: The crop to determine the yields for
        :return: A dict of yields indexed by crop (and compound crop) name
        """
        yields: Dict[str, float] = {}

        for c in self.crops:
            if not c["is_compound"]:
                if c["name"] == target:
                    yields[c["name"]] = c["yield"]
            else:
                if c["plant_1_name"] == target:
                    yields[c["name"]] = c["plant_1_yield"]
                elif c["plant_2_name"] == target:
                    yields[c["name"]] = c["plant_2_yield"]
        return yields

    def get_plants(self) -> List[str]:
        return [p["name"] for p in self.crops]

    def get_crop_by_name(self, name: str) -> Crop:
        return [c for c in self.crops if c["name"] == name][0]

    def get_non_compound_plant_names(self) -> List[str]:
        return [p["name"] for p in self.crops if not p["is_compound"]]

    def get_greywater_plants(self) -> List[Crop]:
        return [c for c in self.crops if c["greywater_ok"]]

    def get_plant_weeks(self) -> List[str]:
        return [f"{c}_week_{w}" for c in self.get_plants() for w in range(cast(int, self.garden["weeks"]))]

    def get_weeks_for_plant(self, plant: Crop) -> List[str]:
        return [f"{plant['name']}_week_{w}" for w in range(cast(int, self.garden["weeks"]))]
