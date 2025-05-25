import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime, date
import copy
import re

# --- MongoDB Setup ---
client = MongoClient(st.secrets["mongo_uri"])
db = client["seamaster"]
collection = db["shipments"]

# --- Page Config and Styling ---
st.set_page_config(layout="wide")

st.markdown(
    """
    <style>
        .block-container {
            padding: 2rem 3rem;
        }
        .stDataFrame, .stDataEditor {
            font-size: 16px;
        }
        thead tr th {
            font-weight: bold;
            font-size: 16px;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🚛 Edit Shipment by Unique ID")
st.markdown("Search and edit truck shipment data by providing a unique shipment ID.")

# --- Auto Status Update Logic ---
def get_truck_status(truck_data):
    """Determines the status of a truck based on its date fields and locations."""

    # --- IMMEDIATE STATUS CHECKS (Highest Priority) ---
    if truck_data.get("Cancel"):
        return "Cancelled"
    if truck_data.get("Flag"):
        return "Flagged"

    arrived_at_loading_point = truck_data.get("Arrived at Loading point")
    loaded_date = truck_data.get("Loaded Date")
    dispatch_date = truck_data.get("Dispatch date")
    date_arrived = truck_data.get("Date Arrived")
    date_offloaded = truck_data.get("Date offloaded")
    load_location = truck_data.get("Load Location", "N/A")
    destination = truck_data.get("Destination", "N/A")
    
    # Correctly access the Borders object from the truck_data
    borders_data = truck_data.get("Borders", {})

    # Helper to check if a date exists and is valid (not None and not pd.NaT)
    def is_valid_date(d):
        return d is not None and not pd.isna(d)

    # Convert date objects to datetime if they are dates, otherwise keep as None or original value
    def to_datetime_if_date(d):
        if isinstance(d, date):
            return datetime.combine(d, datetime.min.time())
        # If it's pd.NaT, an empty string, or None, return None
        if pd.isna(d) or d is None or d == "":
            return None
        return d # Return other types as-is if they are not dates or common nulls
    
    # Convert main dates to datetime for comparison
    arrived_at_loading_point_dt = to_datetime_if_date(arrived_at_loading_point)
    loaded_date_dt = to_datetime_if_date(loaded_date)
    dispatch_date_dt = to_datetime_if_date(dispatch_date)
    date_arrived_dt = to_datetime_if_date(date_arrived)
    date_offloaded_dt = to_datetime_if_date(date_offloaded)

    # --- Primary Status Checks (Higher Priority) ---
    if is_valid_date(date_offloaded_dt):
        return "Offloaded"
    elif is_valid_date(date_arrived_dt):
        return "Arrived for off loading"
    
    # --- Border-related Checks (ONLY if dispatch_date exists) ---
    if is_valid_date(dispatch_date_dt):
        # Extract and store border names and their corresponding dates from borders_data
        parsed_borders = {} 
        for key, value in borders_data.items():
            # Updated regex to capture any border name
            match_arrival = re.match(r"Actual arrival at (.+)", key)
            match_dispatch = re.match(r"Actual dispatch from (.+)", key) 

            if match_arrival:
                border_name = match_arrival.group(1).strip()
                # Use to_datetime_if_date to ensure proper None handling
                parsed_borders.setdefault(border_name, {})["arrival_date"] = to_datetime_if_date(value)
            elif match_dispatch:
                border_name = match_dispatch.group(1).strip()
                # Use to_datetime_if_date to ensure proper None handling
                parsed_borders.setdefault(border_name, {})["dispatch_date"] = to_datetime_if_date(value)
        
        # Sort border names based on their arrival date to determine the sequence.
        # Only consider borders that actually have an arrival date for sorting order.
        # This will correctly order borders chronologically based on when the truck arrived at them.
        sorted_border_names = sorted(
            [name for name, data in parsed_borders.items() if is_valid_date(data.get("arrival_date"))],
            key=lambda border_name: parsed_borders[border_name]["arrival_date"]
        )

       # Traverse the borders in reverse to check for "Clearing" status
        for i in range(len(sorted_border_names) - 1, -1, -1):
            border = sorted_border_names[i]
            arrival_dt = parsed_borders[border].get("arrival_date")
            dispatch_dt = parsed_borders[border].get("dispatch_date")

            if is_valid_date(arrival_dt) and not is_valid_date(dispatch_dt):
                return f"Clearing at {border}"

        # Determine "Departing from" and "Enroute to" based on border activity
        last_dispatched_from = None
        next_enroute_to = None

        # Find the last border the truck dispatched from
        for border in reversed(sorted_border_names):
            dispatch_dt = parsed_borders[border].get("dispatch_date")
            if is_valid_date(dispatch_dt):
                last_dispatched_from = border
                break
        
        # Find the next border to which it's enroute
        if last_dispatched_from:
            try:
                idx = sorted_border_names.index(last_dispatched_from)
                if idx + 1 < len(sorted_border_names):
                    next_enroute_to = sorted_border_names[idx + 1]
            except ValueError:
                pass 
        
        # Scenario 1: Truck has dispatched from initial load location but no border activity yet
        if is_valid_date(dispatch_date_dt) and not last_dispatched_from:
            if sorted_border_names:
                return f"Departing from {load_location} enroute to {sorted_border_names[0]}"
            else:
                return f"Departing from {load_location} enroute to {destination}" # If no borders defined, assume enroute to final destination

        # Scenario 2: Truck has dispatched from a border and is enroute to the next border
        if last_dispatched_from and next_enroute_to:
            return f"Departing from {last_dispatched_from} enroute to {next_enroute_to}"
        # Scenario 3: Truck has dispatched from the last border and is enroute to final destination
        elif last_dispatched_from and not next_enroute_to:
            return f"Departing from {last_dispatched_from} enroute to {destination}"

    # --- Pre-Dispatch Statuses (These are checked if dispatch_date is None/NaT) ---
    elif is_valid_date(loaded_date_dt):
        return "Loaded"
    elif is_valid_date(arrived_at_loading_point_dt):
        return "Waiting to load"
    else:
        return "Booked"

# --- Input Unique ID ---
unique_id = st.text_input("🔎 Enter Shipment Unique ID")

if unique_id:
    shipment = collection.find_one({"Unique ID": unique_id})

    if not shipment:
        st.warning(f"No shipment found with Unique ID: {unique_id}")
    else:
        trucks_data = shipment.get("Trucks", [])

        def extract_ordered_keys(data_list, field):
            all_relevant_keys = set()
            for item in data_list:
                if isinstance(item.get(field), dict):
                    all_relevant_keys.update(item[field].keys())
            
            if field == "Borders":
                sorted_keys = sorted(list(all_relevant_keys), key=lambda x: (
                    re.match(r"(?:Actual arrival at|Actual dispatch from) (.+)", x).group(1).strip(),
                    0 if "arrival" in x else 1
                ))
                return sorted_keys
            else:
                return sorted(list(all_relevant_keys))


        border_keys = extract_ordered_keys(trucks_data, "Borders")
        trailer_keys = extract_ordered_keys(trucks_data, "Trailers")

        required_pre_border_date_fields = [
            "Arrived at Loading point", "Loaded Date", "Dispatch date"
        ]
        required_post_border_date_fields = [
            "Date Arrived", "Date offloaded"
        ]
        
        desired_columns = [
            "Truck Number", "Horse Number"
        ] + trailer_keys + [
            "Driver Name", "Passport NO.", "Contact NO.",
            "Tonnage", "ETA", "Status", "Cargo Description",
            "Current Location", "Load Location", "Destination",
        ] + required_pre_border_date_fields + border_keys + required_post_border_date_fields + [
            "Cancel", "Flag", "Comment"
        ]
        
        if "Status" not in desired_columns:
            desired_columns.insert(desired_columns.index("ETA") + 1, "Status")


        flattened_trucks = []
        for truck in trucks_data:
            flat_truck = {}
            for field in required_pre_border_date_fields + required_post_border_date_fields:
                if field not in truck:
                    truck[field] = None

            current_truck_borders = truck.get("Borders", {})

            for col in desired_columns:
                if col in trailer_keys:
                    flat_truck[col] = truck.get("Trailers", {}).get(col, "")
                elif col in border_keys:
                    flat_truck[col] = current_truck_borders.get(col, None)
                else:
                    flat_truck[col] = truck.get(col, None)
            
            flat_truck["Status"] = get_truck_status(truck)
            
            flattened_trucks.append(flat_truck)

        trucks_df = pd.DataFrame(flattened_trucks)

        for col in trucks_df.columns:
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]):
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce").dt.date
            # Explicitly convert "Arrived at Loading point" to date
            elif col == "Arrived at Loading point":
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce").dt.date


        for col in desired_columns:
            if col not in trucks_df.columns:
                trucks_df[col] = None

        trucks_df = trucks_df[desired_columns]

        for col in ["Cancel", "Flag"]:
            if col in trucks_df.columns:
                trucks_df[col] = trucks_df[col].apply(lambda x: True if x else False)

        original_trucks_df = trucks_df.copy()

        # Column Configs - MODIFIED HERE TO EXPLICITLY INCLUDE "Arrived at Loading point"
        column_config = {
            col: st.column_config.DateColumn(label=col, format="YYYY-MM-DD")
            for col in trucks_df.columns
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]) or col in border_keys
            or col == "Arrived at Loading point" # <<< ADDED THIS LINE
        }
        
        column_config["Status"] = st.column_config.TextColumn(
            label="Status",
            disabled=True
        )


        st.markdown("### 📝 Truck Details", help="Editable fields below")
        edited_trucks_df = st.data_editor(
            trucks_df,
            use_container_width=True,
            num_rows="dynamic",
            column_config=column_config,
            key="truck_editor"
        )

        if not original_trucks_df.equals(edited_trucks_df):
            st.warning("You have unsaved changes. Don't forget to click '📎 Save Changes'!', icon='⚠️'")

        if "Status" in edited_trucks_df.columns:
            status_summary = edited_trucks_df["Status"].value_counts()
            if not status_summary.empty:
                st.markdown("### 📊 Truck Status Summary:")
                for label, count in status_summary.items():
                    st.markdown(f"- **{count} truck(s)** — {label}")
        else:
            st.info("No 'Status' column found to summarize.")

        st.markdown("### 🚚 Truck Summary")
        total_trucks = len(edited_trucks_df)
        total_cancelled = edited_trucks_df[edited_trucks_df["Cancel"] == True].shape[0]
        
        total_on_route = edited_trucks_df[edited_trucks_df["Status"].str.contains("enroute|Clearing", case=False, na=False)].shape[0]
        total_at_destination = edited_trucks_df[edited_trucks_df["Status"] == "Arrived for off loading"].shape[0]


        st.markdown(f"- **Total Trucks**: {total_trucks}")
        st.markdown(f"- **Cancelled Trucks**: {total_cancelled}")
        st.markdown(f"- **Trucks Enroute (including Clearing)**: {total_on_route}")
        st.markdown(f"- **Trucks Arrived for Offloading**: {total_at_destination}")


        st.divider()

        if st.button("📎 Save Changes"):
            try:
                updated_trucks = []

                for i, edited_row_dict in enumerate(edited_trucks_df.to_dict(orient="records")):
                    if all((val == "" or val == 0 or pd.isna(val) or val is None) for key, val in edited_row_dict.items() if key not in ["Cancel", "Flag"]):
                        continue

                    edited_row = copy.deepcopy(edited_row_dict)

                    trailers_data = {key: edited_row.pop(key) for key in trailer_keys if key in edited_row}
                    
                    borders_data = {}
                    for key in border_keys:
                        if key in edited_row:
                            edited_val = edited_row.pop(key)
                            
                            if pd.isna(edited_val) or edited_val is None or edited_val == "":
                                borders_data[key] = None
                            elif isinstance(edited_val, date):
                                borders_data[key] = datetime.combine(edited_val, datetime.min.time())
                            else:
                                borders_data[key] = edited_val

                    cleaned_row = {}
                    for key, val in edited_row.items():
                        if pd.isna(val) or val is None or val == "":
                            cleaned_row[key] = None
                        elif isinstance(val, date):
                            cleaned_row[key] = datetime.combine(val, datetime.min.time())
                        else:
                            cleaned_row[key] = val
                    
                    cleaned_row["Trailers"] = trailers_data
                    cleaned_row["Borders"] = borders_data
                    
                    cleaned_row["Status"] = get_truck_status(cleaned_row)

                    if i < len(trucks_data):
                        original = copy.deepcopy(trucks_data[i])
                        if "_id" in original:
                            del original["_id"] 
                        
                        for key, value in cleaned_row.items():
                            if key == "Trailers":
                                original.setdefault("Trailers", {}).update(value)
                            elif key == "Borders":
                                original.setdefault("Borders", {}).update(value)
                            else:
                                original[key] = value
                        updated_trucks.append(original)
                    else:
                        if not trucks_data:
                            new_truck = {
                                "Truck Number": None, "Horse Number": None, "Driver Name": None,
                                "Passport NO.": None, "Contact NO.": None, "Tonnage": None,
                                "ETA": None, "Status": None, "Cargo Description": None,
                                "Current Location": None, "Load Location": None, "Destination": None,
                                "Arrived at Loading point": None, "Loaded Date": None,
                                "Dispatch date": None, "Date Arrived": None, "Date offloaded": None,
                                "Cancel": False, "Flag": False, "Comment": None,
                                "Trailers": {}, "Borders": {}
                            }
                        else:
                            base_schema = copy.deepcopy(trucks_data[0])
                            for k in base_schema:
                                if k not in ["_id", "Trailers", "Borders"]:
                                    base_schema[k] = None
                            if "_id" in base_schema:
                                del base_schema["_id"]
                            new_truck = base_schema

                        new_truck.update(cleaned_row)
                        updated_trucks.append(new_truck)
                
                final_trucks_to_save = [
                    truck for truck in updated_trucks 
                    if not all((val == "" or val == 0 or val is None or pd.isna(val) or (isinstance(val, dict) and not val)) for key, val in truck.items() if key not in ["Cancel", "Flag"])
                ]


                result = collection.update_one(
                    {"Unique ID": unique_id},
                    {"$set": {"Trucks": final_trucks_to_save}}
                )

                if result.modified_count >= 0:
                    st.success("✅ Shipment updated successfully. Refreshing data...")
                    st.rerun()
                else:
                    st.info("ℹ️ No changes were made.")

            except Exception as e:
                st.error(f"❌ Failed to update shipment: {e}")