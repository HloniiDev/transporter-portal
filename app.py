import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime, date
import copy
import re

# --- MongoDB Setup ---
# Ensure your MongoDB URI is correctly configured in Streamlit secrets.
# Example: mongo_uri = "mongodb+srv://user:pass@cluster.mongodb.net/mydatabase?retryWrites=true&w=majority"
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
    date_arrived = truck_data.get("Date Arrived") # Arrived at final destination for offloading
    date_offloaded = truck_data.get("Date offloaded")

    load_location = truck_data.get("Load Location", "N/A")
    destination = truck_data.get("Destination", "N/A")

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
        parsed_borders = {}
        # This will hold the border names in the order they appear as keys in the Borders object.
        # This is crucial for reflecting the logical sequence you've defined in the DB.
        ordered_border_names = []
        unique_names_added = set()

        # Iterate through the actual keys of borders_data to preserve order
        for key in borders_data.keys():
            match_arrival = re.match(r"Actual arrival at (.+)", key)
            match_dispatch = re.match(r"Actual dispatch from (.+)", key)

            if match_arrival:
                border_name = match_arrival.group(1).strip()
                parsed_borders.setdefault(border_name, {})["arrival_date"] = to_datetime_if_date(borders_data[key])
                if border_name not in unique_names_added:
                    ordered_border_names.append(border_name)
                    unique_names_added.add(border_name)
            elif match_dispatch:
                border_name = match_dispatch.group(1).strip()
                parsed_borders.setdefault(border_name, {})["dispatch_date"] = to_datetime_if_date(borders_data[key])
                if border_name not in unique_names_added:
                    ordered_border_names.append(border_name)
                    unique_names_added.add(border_name)
        
        last_cleared_border = None

        # Iterate through the ordered borders to determine status
        for i, border_name in enumerate(ordered_border_names):
            arrival_dt = parsed_borders.get(border_name, {}).get("arrival_date")
            dispatch_dt = parsed_borders.get(border_name, {}).get("dispatch_date")

            if is_valid_date(arrival_dt) and not is_valid_date(dispatch_dt):
                # Truck arrived at this border but hasn't dispatched yet
                return f"Clearing at {border_name}"
            elif is_valid_date(arrival_dt) and is_valid_date(dispatch_dt):
                # Truck arrived and dispatched from this border
                last_cleared_border = border_name
                # Continue to the next border in the sequence
                continue
            elif not is_valid_date(arrival_dt) and not is_valid_date(dispatch_dt):
                # Truck has not yet arrived at this border.
                # If we've dispatched from the load location or a previous border,
                # this is the next border the truck is enroute to.
                if last_cleared_border: # Dispatched from a previous border
                    return f"Departing from {last_cleared_border} enroute to {border_name}"
                else: # Dispatched from load location, this is the first border in the path
                    return f"Departing from {load_location} enroute to {border_name}"
        
        # If the loop completes, it means the truck has either:
        # 1. Dispatched from the last defined border (last_cleared_border would be set)
        # 2. Dispatched from load location, but no borders were defined (ordered_border_names is empty)
        
        if last_cleared_border:
            # Dispatched from the last border in the sequence
            return f"Departing from {last_cleared_border} enroute to {destination}"
        elif is_valid_date(dispatch_date_dt) and not ordered_border_names:
            # Dispatched from load location, but no borders defined
            return f"Departing from {load_location} enroute to {destination}"


    # --- Pre-Dispatch Statuses (These are checked if dispatch_date is None/NaT) ---
    elif is_valid_date(loaded_date_dt):
        return "Loaded"
    elif is_valid_date(arrived_at_loading_point_dt):
        return "Waiting to load"
    else:
        return "Booked" # Default status if no other conditions are met

# --- Input Unique ID ---
unique_id = st.text_input("🔎 Enter Shipment Unique ID")

if unique_id:
    shipment = collection.find_one({"Unique ID": unique_id})

    if not shipment:
        st.warning(f"No shipment found with Unique ID: {unique_id}")
    else:
        trucks_data = shipment.get("Trucks", [])

        # This function extracts and orders keys for display in st.data_editor.
        # It prioritizes the order found in a sample MongoDB document's dictionary keys.
        def extract_ordered_keys(data_list, field):
            if field == "Borders":
                # Find a sample truck with border data to infer order
                sample_borders_keys = []
                for item in data_list:
                    if isinstance(item.get("Borders"), dict) and item["Borders"]:
                        sample_borders_keys = list(item["Borders"].keys())
                        break # Found a sample, use its keys for initial order

                # Collect all unique border keys across all trucks
                all_unique_border_keys = set()
                for item in data_list:
                    if isinstance(item.get("Borders"), dict):
                        all_unique_border_keys.update(item["Borders"].keys())

                # Start with the order from the sample, then add any missing keys
                ordered_keys = []
                for key in sample_borders_keys:
                    if key in all_unique_border_keys:
                        ordered_keys.append(key)
                        all_unique_border_keys.discard(key) # Remove to track remaining

                # Add any remaining keys that were not in the sample (sorted to be consistent)
                ordered_keys.extend(sorted(list(all_unique_border_keys)))

                return ordered_keys
            else: # For other fields like 'Trailers', just sort alphabetically
                all_relevant_keys = set()
                for item in data_list:
                    if isinstance(item.get(field), dict):
                        all_relevant_keys.update(item[field].keys())
                return sorted(list(all_relevant_keys))


        border_keys = extract_ordered_keys(trucks_data, "Borders")
        trailer_keys = extract_ordered_keys(trucks_data, "Trailers")

        required_pre_border_date_fields = [
            "Arrived at Loading point", "Loaded Date", "Dispatch date"
        ]
        required_post_border_date_fields = [
            "Date Arrived", "Date offloaded"
        ]

        # Define the desired order of columns for the DataFrame
        desired_columns = [
            "Truck Number", "Horse Number"
        ] + trailer_keys + [
            "Driver Name", "Passport NO.", "Contact NO.",
            "Tonnage", "ETA", "Status", "Cargo Description",
            "Current Location", "Load Location", "Destination",
        ] + required_pre_border_date_fields + border_keys + required_post_border_date_fields + [
            "Cancel", "Flag", "Comment"
        ]

        # Ensure "Status" is correctly placed if not already in desired_columns
        if "Status" not in desired_columns:
            # Insert after ETA if ETA is present, otherwise at a logical default
            if "ETA" in desired_columns:
                desired_columns.insert(desired_columns.index("ETA") + 1, "Status")
            else:
                desired_columns.insert(0, "Status") # Fallback to beginning if ETA not found


        flattened_trucks = []
        for truck in trucks_data:
            flat_truck = {}
            # Initialize all date fields that might be missing
            for field in required_pre_border_date_fields + required_post_border_date_fields:
                if field not in truck:
                    truck[field] = None

            current_truck_borders = truck.get("Borders", {})

            # Populate flat_truck with data based on desired_columns order
            for col in desired_columns:
                if col in trailer_keys:
                    flat_truck[col] = truck.get("Trailers", {}).get(col, "")
                elif col in border_keys:
                    flat_truck[col] = current_truck_borders.get(col, None)
                else:
                    flat_truck[col] = truck.get(col, None)
            
            # Recalculate status for each truck based on its current data
            flat_truck["Status"] = get_truck_status(truck)
            
            flattened_trucks.append(flat_truck)

        trucks_df = pd.DataFrame(flattened_trucks)

        # Convert date columns to date objects for Streamlit's DateColumn
        for col in trucks_df.columns:
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]):
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce").dt.date
            # Explicitly convert "Arrived at Loading point" to date as well
            elif col == "Arrived at Loading point":
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce").dt.date

        # Ensure all desired columns exist in the DataFrame, adding as None if missing
        for col in desired_columns:
            if col not in trucks_df.columns:
                trucks_df[col] = None

        # Reorder DataFrame columns to match desired_columns
        trucks_df = trucks_df[desired_columns]

        # Convert Cancel/Flag to boolean for correct display in st.data_editor checkboxes
        for col in ["Cancel", "Flag"]:
            if col in trucks_df.columns:
                trucks_df[col] = trucks_df[col].apply(lambda x: True if x else False)

        # Store a copy of the original DataFrame to detect changes
        original_trucks_df = trucks_df.copy()

        # Define column configurations for st.data_editor
        column_config = {
            col: st.column_config.DateColumn(label=col, format="YYYY-MM-DD")
            for col in trucks_df.columns
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]) or col in border_keys
            or col == "Arrived at Loading point"
        }
        
        column_config["Status"] = st.column_config.TextColumn(
            label="Status",
            disabled=True # Status is auto-calculated, so it should not be editable
        )
        # Configure other specific column types if needed (e.g., NumberColumn for Tonnage, TextColumn for Driver Name etc.)
        # For simplicity, default text/number columns are usually handled well by st.data_editor without explicit config.

        st.markdown("### 📝 Truck Details", help="Editable fields below")
        edited_trucks_df = st.data_editor(
            trucks_df,
            use_container_width=True,
            num_rows="dynamic", # Allows adding/removing rows
            column_config=column_config,
            key="truck_editor"
        )

        # Warn user about unsaved changes
        if not original_trucks_df.equals(edited_trucks_df):
            st.warning("You have unsaved changes. Don't forget to click '📎 Save Changes'!", icon='⚠️')

        # Display status summary
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
        
        # Count trucks "enroute" including those "Clearing" at a border
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
                    # Skip rows that are entirely empty (e.g., newly added empty rows)
                    if all((val == "" or val == 0 or pd.isna(val) or val is None) for key, val in edited_row_dict.items() if key not in ["Cancel", "Flag"]):
                        continue

                    edited_row = copy.deepcopy(edited_row_dict)

                    # Separate Trailers and Borders data back into nested dictionaries
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

                    # Clean up other fields (convert NaT/empty strings to None, convert dates to datetime)
                    cleaned_row = {}
                    for key, val in edited_row.items():
                        if pd.isna(val) or val is None or val == "":
                            cleaned_row[key] = None
                        elif isinstance(val, date):
                            cleaned_row[key] = datetime.combine(val, datetime.min.time())
                        else:
                            cleaned_row[key] = val
                    
                    # Re-assign nested dictionaries
                    cleaned_row["Trailers"] = trailers_data
                    cleaned_row["Borders"] = borders_data
                    
                    # Recalculate status just before saving to ensure it's up-to-date with current data
                    cleaned_row["Status"] = get_truck_status(cleaned_row)

                    # Handle existing vs. new trucks
                    if i < len(trucks_data):
                        # Update existing truck's data
                        original = copy.deepcopy(trucks_data[i])
                        if "_id" in original:
                            del original["_id"] # Remove MongoDB _id before merging/updating
                        
                        # Merge updated data into original, ensuring nested structures are handled
                        for key, value in cleaned_row.items():
                            if key == "Trailers":
                                original.setdefault("Trailers", {}).update(value)
                            elif key == "Borders":
                                original.setdefault("Borders", {}).update(value)
                            else:
                                original[key] = value
                        updated_trucks.append(original)
                    else:
                        # This is a new truck added via 'Add row'
                        # Create a base schema for a new truck if no existing trucks, otherwise copy schema from first truck
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
                            # Create a new truck with a schema similar to existing trucks
                            base_schema = copy.deepcopy(trucks_data[0])
                            for k in base_schema:
                                if k not in ["_id", "Trailers", "Borders"]:
                                    base_schema[k] = None
                            if "_id" in base_schema:
                                del base_schema["_id"]
                            base_schema["Trailers"] = {}
                            base_schema["Borders"] = {}
                            new_truck = base_schema

                        new_truck.update(cleaned_row)
                        updated_trucks.append(new_truck)
                
                # Filter out any completely empty new rows that might have been added
                final_trucks_to_save = [
                    truck for truck in updated_trucks 
                    if not all((val == "" or val == 0 or val is None or pd.isna(val) or (isinstance(val, dict) and not val)) for key, val in truck.items() if key not in ["Cancel", "Flag"])
                ]

                # Update the shipment in MongoDB
                result = collection.update_one(
                    {"Unique ID": unique_id},
                    {"$set": {"Trucks": final_trucks_to_save}}
                )

                if result.modified_count >= 0: # modified_count can be 0 if no actual changes were made to the document
                    st.success("✅ Shipment updated successfully. Refreshing data...")
                    st.rerun() # Rerun to show updated data and status
                else:
                    st.info("ℹ️ No changes were made to the shipment document.")

            except Exception as e:
                st.error(f"❌ Failed to update shipment: {e}")