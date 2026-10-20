import json
import pandas as pd
import os


def extract_surface_line(txt_file_path, excel_file_path):
    """
    Parses a D-Stability geometry TXT (JSON) file, extracts all layers,
    and saves them as separate sheets in an Excel file.

    Args:
        txt_file_path (str): The path to the input .txt file.
        excel_file_path (str): The path for the output .xlsx file.
    """
    try:
        with open(txt_file_path, "r") as f:
            data = json.load(f)

        # Use a dictionary to store points for each layer label
        layers_data = {}

        for i, layer in enumerate(data["Data"]["Layers"]):
            label = layer.get("Label", f"unnamed_layer_{i}")
            points = layer.get("Points", [])

            # Create a DataFrame for the current set of points
            if not points:
                continue

            df_points = pd.DataFrame(points)
            # Ensure columns are named consistently
            if "X" in df_points.columns and "Z" in df_points.columns:
                df_points = df_points[["X", "Z"]]
            else:
                print(
                    f"Skipping layer '{label}' as it does not contain 'X' and 'Z' columns."
                )
                continue

            # If the label already exists, append the new points
            if label in layers_data:
                layers_data[label] = pd.concat(
                    [layers_data[label], df_points], ignore_index=True
                )
            else:
                layers_data[label] = df_points

        # Write the data to an Excel file with each label on a separate sheet
        with pd.ExcelWriter(excel_file_path, engine="openpyxl") as writer:
            for label, df in layers_data.items():
                # Sanitize sheet title if necessary (Excel has restrictions)
                safe_label = (
                    label.replace("[", "")
                    .replace("]", "")
                    .replace("*", "")
                    .replace(":", "")
                    .replace("?", "")
                    .replace("/", "")
                    .replace("\\", "")
                )  # Corrected escaping for backslash
                safe_label = safe_label[:31]  # Max 31 chars for sheet titles

                # Deduplicate points and sort by X to make plotting easier
                df_sorted = df.drop_duplicates().sort_values(by="X")

                df_sorted.to_excel(writer, sheet_name=safe_label, index=False)

        print(
            f"Successfully extracted {len(layers_data)} layers to '{excel_file_path}'"
        )

    except FileNotFoundError:
        print(f"Error: The file '{txt_file_path}' was not found.")
    except json.JSONDecodeError:
        print(
            f"Error: Could not decode JSON from '{txt_file_path}'. Please ensure it is a valid JSON file."
        )
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    # Get the directory where the script is located to ensure files are found
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Define input and output file paths relative to the script's location
    input_file = os.path.join(script_dir, "dijkopbouw_profiel3.txt")
    output_file = os.path.join(script_dir, "dijkopbouw_profiel3.xlsx")

    # Run the extraction function
    extract_surface_line(input_file, output_file)
