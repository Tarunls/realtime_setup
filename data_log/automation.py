import os
import hashlib
import base64
from azure.storage.blob import BlobServiceClient, ContentSettings

def compute_local_md5(file_path):
    """
    Calculates the MD5 hash of a local file.
    Returns the hash as a byte array (to match Azure's format).
    """
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.digest()

def sync_to_azure(connection_string, container_name, source_folder):
    try:
        # 1. Connect to Azure
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service_client.get_container_client(container_name)
        
        # Check if container exists
        if not container_client.exists():
            print(f"Container '{container_name}' does not exist. Creating it...")
            container_client.create_container()

    except Exception as e:
        print(f"Connection Error: {e}")
        return

    print(f"Scanning container '{container_name}'...")

    # 2. Get existing files and their MD5 hashes
    existing_blobs = {}
    blobs_list = container_client.list_blobs()
    
    for blob in blobs_list:
        existing_blobs[blob.name] = blob.content_settings.content_md5

    print(f"Found {len(existing_blobs)} files in Azure.")
    
    local_files = [f for f in os.listdir(source_folder) if os.path.isfile(os.path.join(source_folder, f))]
    
    if not local_files:
        print("No files found in source folder.")
        return

    print(f"Processing {len(local_files)} local files...\n")
    
    upload_count = 0
    skip_count = 0

    for filename in local_files:
        local_path = os.path.join(source_folder, filename)
        local_md5_bytes = compute_local_md5(local_path)
        
        should_upload = False
        reason = ""

        # Logic to decide if we upload
        if filename not in existing_blobs:
            should_upload = True
            reason = "New file"
        elif existing_blobs[filename] is None:
            should_upload = True
            reason = "Missing remote hash"
        elif existing_blobs[filename] != local_md5_bytes:
            should_upload = True
            reason = "Content changed"
        else:
            should_upload = False
            reason = "Identical"

        if should_upload:
            print(f"Uploading: {filename} ({reason})...")
            try:
                with open(local_path, "rb") as data:
                    container_client.upload_blob(
                        name=filename,
                        data=data,
                        overwrite=True,
                        content_settings=ContentSettings(content_md5=local_md5_bytes)
                    )
                
                # If we reach this line, the upload was 100% successful.
                os.remove(local_path)
                print(f" -> Success! Uploaded and deleted locally.")
                upload_count += 1

            except Exception as e:
                # If the upload fails, it jumps here and DOES NOT delete the file.
                print(f" -> Failed to upload (File Kept Locally): {e}")
        else:
            # If the file is already in Azure and perfectly identical, we should delete it 
            # locally so the folder effectively empties out.
            print(f"Already in Azure: {filename} (Identical). Deleting local copy...")
            try:
                os.remove(local_path)
                skip_count += 1
            except Exception as e:
                print(f" -> Failed to delete local file: {e}")

    print("-" * 30)
    print(f"Sync Complete. Uploaded & Deleted: {upload_count} | Already Azure & Deleted: {skip_count}")

if __name__ == "__main__":
    # --- CONFIGURATION ---
    # REDACTED - Rotate your keys in the Azure Portal immediately!
    AZURE_CONNECTION_STRING=""
    CONTAINER_NAME = "scintpi"   # The name of your container (folder in the cloud)
    LOCAL_FOLDER = "./RT"
    
    sync_to_azure(AZURE_CONNECTION_STRING, CONTAINER_NAME, LOCAL_FOLDER)