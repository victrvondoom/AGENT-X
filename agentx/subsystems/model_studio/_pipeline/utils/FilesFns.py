import json
from typing import Any, Dict, Iterator
import zipfile
import os
from pathlib import Path
from typing import Union
#PERMANENT FUNCTION TO ZIP A DIRECTORY
def zip_directory_by_path(source_dir: str, output_zip_path: str):
    """
    Creates a ZIP archive from a given source directory, ensuring the 
    folder structure inside the ZIP file is relative to the source directory.

    Args:
        source_dir (str): The path to the directory to be compressed.
        output_zip_path (str): The desired path and name for the output .zip file.
    """
    # 1. Prepare Path Objects
    source_path = Path(source_dir)
    output_path = Path(output_zip_path)

    if not source_path.is_dir():
        print(f"Error: Source directory not found at '{source_dir}'")
        return

    print(f"Starting compression for directory: '{source_dir}'")

    # 2. Create the ZIP Archive
    # 'w' mode for writing, ZIP_DEFLATED for compression
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        
        # 3. Traverse the Directory Tree
        # os.walk yields (current_folder_path, sub_folder_names, file_names)
        for folder_path, _, filenames in os.walk(source_dir):
            
            # Calculate the relative path from the source_dir to the current folder.
            # This path is used as the directory structure INSIDE the ZIP file.
            relative_to_source = Path(folder_path).relative_to(source_path)

            # 4. Write Each File
            for filename in filenames:
                # Full absolute path on disk
                file_abs_path = Path(folder_path) / filename
                
                # Full path inside the ZIP archive (e.g., 'logs/debug.txt')
                file_zip_path = relative_to_source / filename

                # zipf.write takes the absolute path and stores it at the arcname path
                zipf.write(
                    file_abs_path,
                    arcname=file_zip_path
                )
                print(f"  -> Added: {file_zip_path}")

    print(f"\n✅ Compression complete. Archive saved to: '{output_zip_path}'")

#TEMPLATE FUNCTION TO STREAM A FILE IN CHUNKS
# --- 1. UTILITY FUNCTION: ZIP COMPRESSION ---

def zip_directory(source_dir: str):
    """
    Creates a temporary ZIP file from the source directory and returns the path.
    """
    source_path = Path(source_dir)
    
    # Create a temporary, unique ZIP filename
    zip_filename = f"{source_path.name}_archive_{os.getpid()}.zip"
    output_zip_path = Path("/tmp") / zip_filename if os.name != 'nt' else Path(zip_filename)

    if not source_path.is_dir():
        raise FileNotFoundError(f"Source directory not found at '{source_dir}'")

    with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for folder_path, _, filenames in os.walk(source_dir):
            relative_to_source = Path(folder_path).relative_to(source_path)
            
            for filename in filenames:
                file_abs_path = Path(folder_path) / filename
                file_zip_path = relative_to_source / filename

                zipf.write(file_abs_path, arcname=file_zip_path)

    return output_zip_path

# --- 2. STREAMING GENERATOR ---

def file_iterator(file_path: Path, chunk_size: int = 8192):
    """
    Generator that reads a file in chunks for streaming.
    """
    with open(file_path, mode="rb") as file_like:
        chunk = file_like.read(chunk_size)
        while chunk:
            yield chunk
            chunk = file_like.read(chunk_size)


#COUNT NUMBER OF FILES IN A DIRECTORY
def count_subfolders(directory_path: Union[str, Path]):
    """
    Returns the count of immediate sub-directories inside the given path.
    Returns 0 if the path is not a directory or an error occurs.
    """
    path = Path(directory_path)

    if not path.is_dir():
        # Path does not exist or is not a directory
        return 0

    folder_count = 0
    try:
        # os.scandir is efficient as it fetches file type information immediately.
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir():
                    folder_count += 1
        return folder_count
    
    except Exception:
        # Handles exceptions like PermissionError or OSError
        return 0