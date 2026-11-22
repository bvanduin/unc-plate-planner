import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

LOCATIONS = {
    "lenoir": {
        "name": "Top of Lenoir",
        "url": "https://dining.unc.edu/locations/top-of-lenoir/",
        "desc": "North Campus · Top of Lenoir (Live CDS Menu)"
    },
    "chase": {
        "name": "Chase Dining Hall",
        "url": "https://dining.unc.edu/locations/chase/",
        "desc": "South Campus · Chase Hall (Live CDS Menu)"
    }
}

# Keyword rules mapping live CDS dish names to SIMAP Coolfood Pledge (2025) factors:
# (regex_pattern, coolfood_category, group_key, default_grams, kg_co2e_per_kg, swap_set)
COOLFOOD_RULES = [
    # High-impact meats
    (r"\b(beef|burger|cheeseburger|meatball|steak|brisket|barbacoa)\b", "Beef & buffalo meat", "meat", 113, 41.35, "protein"),
    (r"\b(lamb|gyro|mutton)\b", "Lamb/mutton & goat meat", "meat", 113, 41.62, "protein"),
    (r"\b(pork|bacon|ham|sausage|carnitas|pepperoni|pulled pork)\b", "Pork (pig meat)", "meat", 85, 9.83, "protein"),
    (r"\b(shrimp|prawn|crab|lobster)\b", "Crustaceans", "meat", 85, 21.13, "protein"),
    # Moderate meats & seafood
    (r"\b(chicken|turkey|poultry|wings|nugget|tender)\b", "Poultry", "meat", 113, 4.40, "protein"),
    (r"\b(fish|catfish|salmon|tilapia|cod|tuna|pollock)\b", "Fish (finfish)", "meat", 113, 4.98, "protein"),
    # Plant-forward proteins
    (r"\b(tofu|tempeh|edamame|soy)\b", "Soybeans/Tofu", "plant", 113, 1.75, "protein"),
    (r"\b(impossible|beyond|plant[- ]based|vegan sausage|black bean burger)\b", "Plant-based alternatives", "plant", 95, 2.05, "protein"),
    (r"\b(bean|chickpea|lentil|hummus|falafel|black beans|red beans)\b", "Beans and pulses (dried)", "plant", 60, 1.68, "protein"),
    # Dairy & eggs
    (r"\b(omelet|scrambled egg|boiled egg|fried egg|eggs|frittata)\b", "Eggs", "dairy", 100, 3.66, "protein"),
    (r"\b(cheese|cheddar|mozzarella|parmesan|quesadilla|mac & cheese|macaroni)\b", "Cheese", "dairy", 45, 8.69, ""),
    (r"\b(yogurt|parfait|cottage cheese)\b", "Yogurt", "dairy", 170, 2.93, ""),
    (r"\b(oatmilk|oat milk)\b", "Oat milk", "dairy", 245, 0.89, "milk"),
    (r"\b(soymilk|soy milk)\b", "Soy milk", "dairy", 245, 0.48, "milk"),
    (r"\b(cow's milk|whole milk|2%.*milk|skim milk|chocolate milk)\b", "Milk (cow's milk)", "dairy", 245, 2.17, "milk"),
    # Grains & baked goods
    (r"\b(pizza)\b", "Wheat/Rye & Cheese blend", "grain", 115, 3.35, ""),
    (r"\b(rice|fried rice|sushi|roll)\b", "Rice", "grain", 55, 2.53, ""),
    (r"\b(oatmeal|oats|grits|chia)\b", "Oats / Maize", "grain", 45, 2.30, ""),
    (r"\b(potato|potatoes|fries|hash brown|hashbrown|mash|tater)\b", "Potatoes", "grain", 120, 0.55, ""),
    (r"\b(waffle|pancake|french toast|biscuit|bread|bagel|toast|pasta|penne|spaghetti|noodle|croissant|muffin)\b", "Wheat/Rye (bread, pasta, baked goods)", "grain", 70, 1.52, ""),
    # Vegetables & fruit
    (r"\b(broccoli|brussel|cabbage|kale|cauliflower|slaw)\b", "Cabbages and other Brassicas", "veg", 85, 0.62, ""),
    (r"\b(tomato|tomatoes|marinara)\b", "Tomatoes", "veg", 90, 0.69, ""),
    (r"\b(carrot|sweet potato|beet|turnip|autumn veg)\b", "Root Vegetables", "veg", 85, 0.31, ""),
    (r"\b(corn)\b", "Maize (corn)", "veg", 100, 0.55, ""),
    (r"\b(salad|greens|spinach|zucchini|squash|green bean|okra|mushroom|pepper|vegetable|veggie|eggplant)\b", "Other vegetables", "veg", 90, 0.50, ""),
    (r"\b(berry|berries|strawberry|blueberry|raspberry)\b", "Berries", "fruit", 75, 1.65, ""),
    (r"\b(orange|citrus|grapefruit)\b", "Citrus Fruit", "fruit", 90, 0.39, ""),
    (r"\b(apple|applesauce)\b", "Apples", "fruit", 130, 0.36, ""),
    (r"\b(banana|plantain)\b", "Bananas", "fruit", 110, 0.71, ""),
    (r"\b(melon|cantaloupe|honeydew|watermelon|pineapple)\b", "Melons & Tropical Fruit", "fruit", 140, 0.45, ""),
    # Beverages
    (r"\b(coffee|espresso|latte)\b", "Coffee", "drink", 10, 16.70, "")
]

# Skip pure condiments/toppings that clutter a plate planner
SKIP_WORDS = re.compile(
    r"\b(ketchup|mustard|mayonnaise|syrup|sprinkles|salt|pepper|dressing|vinaigrette|sauce|jelly|jam|creamer|sweetener|sugar|water|ice)\b",
    re.I
)

def classify_item(name):
    if SKIP_WORDS.search(name):
        return None
    lower = name.lower()
    for pattern, cat, group, grams, factor, swap in COOLFOOD_RULES:
        if re.search(pattern, lower):
            return {
                "category": cat,
                "group": group,
                "grams": grams,
                "factor": factor,
                "swap": swap
            }
    return None

def scrape_location(hall_key, info, date_str):
    url = f"{info['url']}?date={date_str}"
    headers = {"User-Agent": "UNC-Plate-Planner-Bot/1.0"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    meals_found = {
        "breakfast": [],
        "continental": [],
        "brunch": [],
        "lunch": [],
        "dinner": []
    }
    seen = {k: set() for k in meals_found}

    tabs = [
        t.get_text(" ", strip=True).lower()
        for t in soup.select('[role="tablist"] button, [role="tablist"] a, .menu-tabs li')
    ]
    panels = soup.select('[role="tabpanel"]')

    for idx, panel in enumerate(panels):
        tab_name = tabs[idx] if idx < len(tabs) else ""
        if "continental" in tab_name:
            meal_key = "continental"
        elif "brunch" in tab_name:
            meal_key = "brunch"
        elif "breakfast" in tab_name:
            meal_key = "breakfast"
        elif "lunch" in tab_name and "late" not in tab_name:
            meal_key = "lunch"
        elif "dinner" in tab_name and "late" not in tab_name:
            meal_key = "dinner"
        else:
            continue

        for station_block in panel.find_all(["div", "section"], recursive=True):
            heading = station_block.find(["h2", "h3", "h4"])
            station_name = heading.get_text(strip=True) if heading else "Dining Station"
            if len(station_name) > 45:
                station_name = "Main Line"

            for link in station_block.select("ul li a"):
                raw_name = link.get_text(" ", strip=True)
                if not raw_name or len(raw_name) < 3 or raw_name in seen[meal_key]:
                    continue
                match = classify_item(raw_name)
                if match:
                    seen[meal_key].add(raw_name)
                    item_id = f"{hall_key}_{meal_key}_{len(meals_found[meal_key])}"
                    meals_found[meal_key].append({
                        "id": item_id,
                        "name": raw_name,
                        "station": station_name,
                        "grams": match["grams"],
                        "factor": match["factor"],
                        "category": match["category"],
                        "group": match["group"],
                        "swap": match["swap"]
                    })

    return meals_found

def main():
    today_est = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    output = {
        "updated_at": today_est,
        "source": "Carolina Dining Services (dining.unc.edu)",
        "halls": {}
    }

    for hall_key, info in LOCATIONS.items():
        try:
            meals = scrape_location(hall_key, info, today_est)
        except Exception as e:
            print(f"Warning: failed to scrape {hall_key}: {e}")
            meals = {
                "breakfast": [],
                "continental": [],
                "brunch": [],
                "lunch": [],
                "dinner": []
            }

        output["halls"][hall_key] = {
            "name": info["name"],
            "desc": info["desc"],
            "meals": meals
        }

    total_items = sum(
        len(items)
        for h in output["halls"].values()
        for items in h["meals"].values()
    )
    if total_items > 0:
        with open("menu_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        print(f"Successfully saved {total_items} live CDS items for {today_est}.")
    else:
        print("No active meal items returned today (dining hall may be closed); keeping existing menu_data.json.")

if __name__ == "__main__":
    main()
