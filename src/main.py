import getopt
import json
import sys
from typing import List, cast, TYPE_CHECKING

import xlwt  # type: ignore
from pulp import LpProblem, LpMaximize, LpVariable, lpSum, LpAffineExpression  # type: ignore

from src.garden import Garden


def setup_problem(garden: Garden):
    prob: LpProblem = LpProblem("Garden_Problem", LpMaximize)
    plant_vars = LpVariable.dicts("planted", garden.get_plant_weeks(), lowBound=0, cat="Continuous")
    fallow_vars = LpVariable.dicts("fallow", range(num_weeks), lowBound=0, cat="Continuous")

    # Create the objective function (maximize total yield in the garden)
    total_yield: LpAffineExpression = lpSum(
        [c['yield'] * lpSum([plant_vars[c_w] for c_w in garden.get_weeks_for_plant(c)]) for c in garden.crops])
    nitrogen_expression = lpSum(
        [c["delta_n"] * lpSum(plant_vars[v] for v in get_variable_names_for_weeks(c["name"], 0, num_weeks)) for c in
         garden.crops])
    # Add a small penalty for land being left fallow
    fallow: LpAffineExpression = lpSum(0.1 * f for f in fallow_vars.values())
    prob += (total_yield + (0.1 * nitrogen_expression))

    # Create constraints that depend on the week
    for w in range(num_weeks):
        # Land Constraints
        # get all of the planting variables that apply to this week
        week_plantings = [(k, v) for k, v in plant_vars.items() if f"week_{w}" in k]
        prev_planting_expressions: List[LpAffineExpression] = []
        # for every different plant, add area from previous plantings that would overlap with this week.
        # eg, in week 9 for a crop X with a lifespan of 2 weeks, count the variables "X_week_{7 through 9}"
        for crop in garden.crops:
            crops_living_in_week = get_plants_living_in_week(crop, w, plant_vars)
            prev_planting_expressions.append(lpSum(crops_living_in_week))

        prob += lpSum(prev_planting_expressions) + fallow_vars[w] == garden.garden["sqft"], f"land_constraint_week_{w}"

        # Water Constraints
        # All greywater must be used, every week, except the last week
        prob += lpSum([c["water_use"] * lpSum(get_plants_living_in_week(c, w, plant_vars)) for c in
                       garden.get_greywater_plants()]) >= garden.garden[
                    "greywater"], f"use_all_greywater_week_{w}"
        # we can't use water that we don't have
        prob += lpSum(
            [c["water_use"] * lpSum(get_plants_living_in_week(c, w, plant_vars)) for c in garden.crops]) <= \
                garden.garden["greywater"] + garden.garden["rainwater"], f"total_water_constraint_week_{w}"

    # Create the min and max yield constraints per crop
    for yield_spec in garden.garden['yields']:
        target = yield_spec["plant"]
        sum_contributing_crops = lpSum(
            [y * lpSum([plant_vars[v] for v in get_variable_names_for_weeks(c, 0, num_weeks)]) for c, y in
             garden.get_target_yields(target).items()])
        prob += sum_contributing_crops >= yield_spec["min_yield"], f"{target}_min_yield"
        if 'max_yield' in yield_spec:
            prob += sum_contributing_crops <= yield_spec["max_yield"], f"{target}_max_yield"
        if 'max_yield_pct' in yield_spec:
            prob += sum_contributing_crops <= total_yield * yield_spec["max_yield_pct"], f"{target}_max_yield_percent"

    # Add constraint to only allow crops to be planted if there is enough time to harvest them.
    for crop in garden.crops:
        # it is ok to plant cover crops that won't reach maturity
        if not crop["is_cover_crop"]:
            plants_that_overlap = get_plants_living_in_week(crop, num_weeks - 1, plant_vars)
            if crop["lifespan"] <= num_weeks and len(plants_that_overlap) > 0:
                plants_that_overlap = plants_that_overlap[1:]
            prob += lpSum(plants_that_overlap) == 0, f"latest_planting_{crop['name']}"

    # Nutrient Constraints
    prob += nitrogen_expression >= 0, "nitrogen constraints"

    return prob, total_yield, nitrogen_expression, plant_vars, fallow_vars


def get_plant_variable_name(crop: str, week: int):
    return f"{crop}_week_{week}"


def get_variable_names_for_weeks(crop: str, week_start: int, week_end: int) -> List[str]:
    """
    Helper function to get a list of plant & week variable names, starting at week_start and ending at week_end
    """
    return [get_plant_variable_name(crop, w) for w in range(week_start, week_end)]


def get_plants_living_in_week(crop, week, plant_vars):
    """
    helper function to return a list of plantings that would still be alive in the given week. Note that plants planted
    crop["lifespan"] weeks ago will be harvested this week, so they are not included.
    """
    earliest_planting: int = cast(int, (week - crop["lifespan"]) + 1)
    if earliest_planting < 0:
        earliest_planting = 0

    return [plant_vars[v] for v in get_variable_names_for_weeks(crop["name"], earliest_planting, week + 1)]


if __name__ == '__main__':
    garden: Garden = Garden(sys.argv[1], ["data/carrot.json",
                                          "data/tomato.json",
                                          "data/clover.json",
                                          "data/corn.json",
                                          "data/potato.json",
                                          "data/onion.json"])
    out_filename = sys.argv[2]
    num_weeks = cast(int, garden.garden["weeks"])
    epsilon = 0.1

    prob, total_yield, nitrogen_expression, plant_vars, fallow_vars = setup_problem(garden)

    # solve the problem
    prob.solve()
    book = xlwt.Workbook()

    if prob.sol_status != 1:
        print("No optimal solution found")
    else:
        summary = book.add_sheet("summary")
        plantings = book.add_sheet("plantings")

        crop_mapping = {p: garden.get_target_yields(p) for p in garden.get_non_compound_plant_names()}
        unique_crops = {c for t in (crop_yield.keys() for crop_yield in crop_mapping.values()) for c in t}
        print(
            f"Optimal solution found! You can grow approximately {int(total_yield.value())} lbs of food in your {garden.garden['sqft']} sqft garden!")

        # output the summary data
        summary.write(0, 0, "Change in Nitrogen")
        summary.write(0, 1, f"{round(nitrogen_expression.value(), 2)} grams")
        summary.write(1, 0, "Total Yield")
        summary.write(1, 1, f"{int(total_yield.value())} lbs")
        for idx, (t, t_yields) in enumerate(crop_mapping.items(), start=2):
            target_yield_sum = sum(
                int(y * sum(plant_vars[v].varValue for v in get_variable_names_for_weeks(c, 0, num_weeks))) for c, y in
                t_yields.items())
            summary.write(idx, 0, t)
            summary.write(idx, 1, f"{target_yield_sum} lbs", xlwt.easyxf('font: colour green;'))

        plantings.write(1, 0, "fallow")
        for w in range(num_weeks):
            col = w + 1
            plantings.write(0, col, f"Week {w}")
            plantings.write(1, col, f"{round(fallow_vars[w].varValue, 1)} sqft", xlwt.easyxf('font: colour red;'))

            for idx, c in enumerate(unique_crops, start=2):
                instructions = ""
                # Print out what to plant this week
                plant_var = plant_vars[get_plant_variable_name(c, w)]
                pretty_name = f"{c.split('-')[0]} with {c.split('-')[1]}" if "-" in c else c
                # Print the row labels if we're on the first week
                if w == 0:
                    plantings.write(idx, 0, pretty_name)
                if plant_var.varValue >= epsilon:
                    instructions += f"Plant {round(plant_var.varValue, 1)} sqft\t"
                crop = garden.get_crop_by_name(c)
                # If there is anything to be harvested, print that out too
                harvest_from = int((w + 1) - crop["lifespan"])
                if harvest_from >= 0:
                    harvest_var = plant_vars[get_plant_variable_name(c, harvest_from)]
                    if harvest_var.varValue > epsilon:
                        instructions += f"Harvest {round(harvest_var.varValue, 1)} sqft (planted in week {harvest_from})"
                plantings.write(idx, col, instructions)

        # Sensitivity Analysis
        garden.garden["rainwater"] = garden.garden["rainwater"] * 0.5
        prob_s, total_yield_s, nitrogen_expression_s, plant_vars_s, fallow_vars_s = setup_problem(garden)
        prob_s.solve()
        sensitivity = book.add_sheet("sensitivity")
        sensitivity.write(0, 0,
                          f"Results with a 50% reduction in rainwater (using {garden.garden['rainwater']} gal/week)")
        if prob_s.sol_status == 1:
            sensitivity.write(1, 0, "Change in Nitrogen")
            sensitivity.write(1, 1, f"{round(nitrogen_expression_s.value(), 2)} grams")
            sensitivity.write(2, 0, "Total Yield")
            sensitivity.write(2, 1, f"{int(total_yield_s.value())} lbs")
            for idx, (t, t_yields) in enumerate(crop_mapping.items(), start=3):
                target_yield_sum = sum(
                    int(y * sum(plant_vars_s[v].varValue for v in get_variable_names_for_weeks(c, 0, num_weeks))) for c, y in
                    t_yields.items())
                sensitivity.write(idx, 0, t)
                sensitivity.write(idx, 1, f"{target_yield_sum} lbs", xlwt.easyxf('font: colour green;'))
        else:
            sensitivity.write(1, 0, "Model is infeasible")


        book.save(out_filename)