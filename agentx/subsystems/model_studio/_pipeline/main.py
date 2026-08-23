

from http.client import HTTPException
import os
from pathlib import Path
from utils.FilesFns import count_subfolders
from fastapi import FastAPI,status,Query,BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any,Optional
import os
import asyncio
from contextlib import asynccontextmanager

from oumi.inference import VLLMInferenceEngine
from oumi.core.configs import InferenceConfig, ModelParams
from oumi.core.types.conversation import Conversation, Message, Role
# --- OUMI IMPORTS (The Core API) ---

# 2. Initialize FastAPI with the lifespan
# ============================================================================
# LIFESPAN SETUP
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🤖 System waking up... Ready for GRPO training requests.")
    yield
    print("🛑 System shutting down...")

app = FastAPI(lifespan=lifespan)



# --- Request Model ---
current_engine = None
current_loaded_model_key = None # Tuple of (model_name, adapter_location)

class InferenceRequest(BaseModel):
    model_name: str ="HuggingFaceTB/SmolLM2-135M-Instruct"
    adapter_location: Optional[str] = None
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7

class TrainRequest(BaseModel):
    """Request model for GRPO training"""
    model_name: str = "HuggingFaceTB/SmolLM2-135M-Instruct"
    dataset_name: str = "openai/gsm8k"
    output_dir: str = "./output"
    num_epochs: int = 1
    batch_size: int = 2
    learning_rate: float = 1e-6
    max_completion_length: int = 256


class ClusterData(BaseModel):
    clusterID: int
    basemodel: str
    datasets: List[str]

# ----------------------------------------------------
# CORS CONFIGURATION BLOCK
# ----------------------------------------------------

# Define the origins that are allowed to make requests to your API
origins = [
    # 🚨 Add your specific frontend URL here
    "http://localhost:5173",
    # You might also include the standard local host names for flexibility
    "http://127.0.0.1:5173", 
    "*"
    # Optional: If you want to allow requests from any origin (⚠️ DANGEROUS IN PRODUCTION!)
    # "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,              # List of origins that can make requests
    allow_credentials=True,             # Allow cookies to be included in requests
    allow_methods=["*"],                # Allow all methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],                # Allow all headers
)


# ----------------------------------------------------
# TRAINING AND DOWNLOAD ENDPOINTS
# ----------------------------------------------------

@app.get("/")
def read_root():
    return {"Hello": "World to th e main API"}

@app.get("/output/zip/download/{clusterID}/{stepID}")
async def download_folder(clusterID: int,stepID: int):

    """
    Compresses a local folder into a ZIP file and streams it for download.
    """
    # 🚨 Configuration: Define the root where all downloadable folders reside.
    # Adjust this to point to your actual data/config directory.
    BASE_DATA_PATH = Path("../../output") 
    from utils.FilesFns import zip_directory, file_iterator
    # Construct the full path to the folder
    source_dir_path = BASE_DATA_PATH / f"cluster{clusterID}" / f"step_{stepID}"
    print(f"Preparing to compress folder at: {source_dir_path}")

    if not source_dir_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Folder not found on the server."
        )
    try:
        # 1. COMPRESSION: Create the temporary ZIP file
        zip_file_path = zip_directory(str(source_dir_path))
        from fastapi.responses import StreamingResponse
        
        # 2. RESPONSE: Prepare the StreamingResponse
        response = StreamingResponse(
            file_iterator(zip_file_path),
            media_type="application/zip",
            status_code=status.HTTP_200_OK
        )
        
        # 3. SET HEADERS: Tell the browser to download the file
        file_name_for_download = f"{clusterID}_export.zip"
        response.headers["Content-Disposition"] = f"attachment; filename=\"{file_name_for_download}\""
        
        # # 4. CLEANUP: Register a background task to delete the temporary ZIP file
        # @response.background_task
        # def cleanup():
        #     if zip_file_path.exists():
        #         os.remove(zip_file_path)
        #         print(f"\nCleaned up temporary file: {zip_file_path}")
        
        return response

    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        # Catch any unexpected compression errors
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Compression error: {str(e)}")
    
@app.post("/train/withjson")
async def train_fromjson(jsonData:ClusterData):
    # Now data_input is a Python object with type-checked attributes.

    print("Received data and validated successfully.",jsonData)
    from utils.yamlFns import json_to_dict, WriteYAMLFile_train, TrainYAMLFile
    # CONVERT JSON TO DICT
    print("Converting JSON to DICT...")
    dict_output = {"clusterID": jsonData.clusterID,
                   "basemodel": jsonData.basemodel,
                   "datasets": jsonData.datasets}
    print("converted")
    # CONVERT JSON TO YAML FILES AND STORE THEM AT THE CONFIGS
    print("Writing YAML Files...")
    WriteYAMLFile_train(dict_output['clusterID'],dict_output['datasets'],dict_output['basemodel'],f'../../configs/cluster{dict_output["clusterID"]}')
    print("Written")
    # RUN THE YAML FILES AND STORE THEM AT THE OUTPUT
    print("Training from YAML Files...")
    TrainYAMLFile(dict_output['clusterID'], f'../../configs/cluster{dict_output["clusterID"]}')
    print("Trained")
    #Create a ZIP of the OUTPUT FOLDER AND SEND IT BACK TO THE USER
    # from utils.FilesFns import zip_directory_by_path
    # print("Creating ZIP Archive of the output folder...")
    # zip_directory_by_path(f'../../output/cluster{dict_output["clusterID"]}/step_{len(dict_output["datasets"])-1}', f'../../zipped/cluster{dict_output["clusterID"]}_archive.zip')
    # print("ZIP Archive created.")
    # RETURN SUCCESS MESSAGE
    return {"message": "Successfully started training for Cluster ID: "+str(dict_output['clusterID'])}


# ----------------------------------------------------
# EVALUATION AND RESULT ENDPOINTS
# ----------------------------------------------------


@app.get("/evaluate/run/{clusterID}")
async def evaluate_model(clusterID: Optional[int],
    local_filepath: Optional[str] = Query(None), 
    hf_model: str= Query(None),
    mode: int = Query(None),
    ):
    

    print("Received data and validated successfully.",hf_model,clusterID,local_filepath)
    from utils.yamlFns import WriteYAMLFile_eval,EvalYAMLFile
    # GIVEN LOCAL LOCATION OR hf_model NAME OR PATH OF GENERATED OUTPUT 
    adapterLoc=None
    print("Converting JSON to DICT...")
    if mode==1:
        adapterLoc = hf_model
        print(f"Model Source: Hugging Face Pretrained Model: {adapterLoc}")
    elif mode==2:
        adapterLoc = local_filepath
        print(f"Model Source: Explicit Local Path: {adapterLoc}")
    elif mode==3: 
        cluster_path = f"../../output/cluster{clusterID}"
        latest_step_index = count_subfolders(cluster_path) - 1
        if latest_step_index < 0:
             print(f"WARNING: Cluster {clusterID} folder is empty.")
             adapterLoc = None 
        else:
             stepID = latest_step_index 
             adapterLoc = f'{cluster_path}/step_{stepID}'
             print(f"Model Source: Default Cluster Path (Latest Step {stepID}): {adapterLoc}")
    print("converted")
    print(f"Final Model Name to use for evaluation: {adapterLoc}")
    # CONVERT JSON TO YAML FILES AND STORE THEM AT THE CONFIGS
    print("Writing YAML Files...")
    WriteYAMLFile_eval(hf_model,adapterLoc, clusterID)
    print("Written")
    
    # RUN THE YAML FILES AND STORE THEM AT THE OUTPUT
    print("Evaled from YAML Files...")
    EvalYAMLFile(clusterID)
    print("Evaled")

    #return the JSON from the OUTPUT FOLDER
    print("Processing and returning evaluation results...")
    from utils.evalProcessFns import process_cluster_results
    results = process_cluster_results(str(clusterID))    
    return results


# ----------------------------------------------------
# RL SERVER MANAGEMENT ENDPOINTS
# ----------------------------------------------------


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

@app.post("/grpo/train")
async def start_grpo_training(req: TrainRequest, background_tasks: BackgroundTasks):
    """
    Start GRPO training in background.
    
    Args:
        model_name: HuggingFace model ID
        adapter_location: Optional path to LoRA adapter
        dataset_name: Dataset from HuggingFace
        output_dir: Where to save output
        num_epochs: Training epochs
        batch_size: Batch size (must be divisible by num_generations=2)
        learning_rate: Learning rate
        max_completion_length: Max tokens to generate
    
    Example:
        POST /grpo/train
        {
            "model_name": "HuggingFaceTB/SmolLM2-135M-Instruct",
            "adapter_location": null,
            "dataset_name": "openai/gsm8k",
            "output_dir": "./output",
            "num_epochs": 1,
            "batch_size": 2,
            "learning_rate": 1e-6,
            "max_completion_length": 256
        }
    """
    from utils.grpo_trainer import GrpoTrainer

    try:
        # Validate batch size (must be divisible by num_generations=2)
        if req.batch_size % 2 != 0:
            raise HTTPException(
                status_code=400,
                detail=f"batch_size ({req.batch_size}) must be divisible by num_generations (2)"
            )
        
        print(f"\n{'='*80}")
        print(f"🚀 GRPO Training Request Received")
        print(f"{'='*80}")
        print(f"Model: {req.model_name}")
        print(f"Dataset: {req.dataset_name}")
        print(f"Output: {req.output_dir}")
        print(f"Batch size: {req.batch_size}")
        print(f"Learning rate: {req.learning_rate}")
        
        # Run training in background
        background_tasks.add_task(
            GrpoTrainer.train,
            model_name=req.model_name,
            dataset_name=req.dataset_name,
            output_dir=req.output_dir,
            num_epochs=req.num_epochs,
            batch_size=req.batch_size,
            learning_rate=req.learning_rate,
            max_completion_length=req.max_completion_length,
        )
        
        return {
            "status": "started",
            "message": "GRPO training initiated in background",
            "model": req.model_name,
            "output_dir": req.output_dir,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/grpo/train/simple")
async def start_simple_grpo_training(background_tasks: BackgroundTasks):
    """
    Simple GRPO training with default settings.
    """
    from utils.grpo_trainer import GrpoTrainer
    req = TrainRequest(
        model_name="HuggingFaceTB/SmolLM2-135M-Instruct",
        dataset_name="openai/gsm8k",
        output_dir="./output/grpo_default",
    )
    
    background_tasks.add_task(
        GrpoTrainer.train,
        model_name=req.model_name,
        dataset_name=req.dataset_name,
        output_dir=req.output_dir,
        num_epochs=req.num_epochs,
        batch_size=req.batch_size,
        learning_rate=req.learning_rate,
        max_completion_length=req.max_completion_length,
    )
    
    return {"status": "started", "message": "Simple GRPO training started"}



# ----------------------------------------------------
# INFERENCE
# ----------------------------------------------------

def get_engine(model_name: str, adapter_location: Optional[str]):
    """
    Retrieves the inference engine. Reloads only if the requested model/adapter 
    differs from the currently loaded one.
    """
    global current_engine, current_loaded_model_key
    # Oumi Imports
    from oumi.inference import VLLMInferenceEngine
    from oumi.core.configs import ModelParams
    
    
    request_key = (model_name, adapter_location)
    
    # Check if we can reuse the existing engine
    if current_engine is not None and current_loaded_model_key == request_key:
        return current_engine

    print(f"Loading model: {model_name} (Adapter: {adapter_location})...")
    
    # Configure Model Parameters
    # adapter_model can be a local path or a HF Hub ID
    model_params = ModelParams(
        model_name=model_name,
        adapter_model=adapter_location,
        trust_remote_code=True,
        chat_template="chatml", # Optional: Adjust based on your model
    )

    # Initialize the engine (VLLM is recommended for production)
    # Use NativeTextInferenceEngine if VLLM is not available or for CPU testing
    try:
        new_engine = VLLMInferenceEngine(model_params=model_params)
        
        # Update cache
        current_engine = new_engine
        current_loaded_model_key = request_key
        print("Engine loaded successfully.")
        return current_engine
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {str(e)}")

@app.post("/infer/run")

async def generate_text(request: InferenceRequest):
    """
    Endpoint to run inference.
    Receives model_name and adapter_location from the frontend.
    """
    print("importing oumi modules")
    print("Received inference request:", request)
    # Initialize with a small, free model
    engine = VLLMInferenceEngine(
        ModelParams(
            model_name= request.model_name,
            adapter_model= request.adapter_location,
            trust_remote_code=True,
            model_kwargs={"device_map": "auto"}
            
        )
    )

    # Create a conversation
    print("Preparing conversation...")
    conversation = Conversation(
        messages=[Message(role=Role.USER, content="What is Oumi?")]
    )
    print("Conversation prepared:", conversation)
    # Get response
    result = engine.infer([conversation], InferenceConfig())
    print(result)
    return {"response":result[0].messages[-1].content}
    # from oumi.core.types.conversation import Conversation, Message, Role
    # from oumi.core.configs import InferenceConfig, GenerationParams

    # engine = get_engine(request.model_name, request.adapter_location)

    # # Prepare the input conversation
    # input_conversation = Conversation(
    #     messages=[
    #         Message(role=Role.USER, content=request.prompt)
    #     ]
    # )

    # # Configure generation parameters (max tokens, temp, etc.)
    # inference_config = InferenceConfig(
    #     generation=GenerationParams(
    #         max_new_tokens=request.max_tokens,
    #         temperature=request.temperature
    #     )
    # )

    # try:
    #     # Run Inference
    #     # engine.infer expects a list of conversations and returns a list of conversations
    #     outputs = engine.infer(
    #         input=[input_conversation], 
    #         inference_config=inference_config
    #     )
        
    #     # Extract the assistant's response from the last message
    #     response_text = outputs[0].messages[-1].content
    #     return {"response": response_text}

    # except Exception as e:
    #     raise HTTPException(status_code=500, detail=f"Inference failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)