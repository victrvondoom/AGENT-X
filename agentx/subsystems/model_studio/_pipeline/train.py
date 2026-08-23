

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
# --- OUMI IMPORTS (The Core API) ---

# Import custom logic so decorators register the functions
import utils.RL.customRegistery  
# import utils.RL.customRegistery as custom_logic 

# Import our server manager
from utils.RL.serverUtils import ServerManager

server_manager = ServerManager()
# 1. Define the Lifespan Context Manager
# This runs automatically when the API starts and stops.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🟢 STARTUP logic (optional)
    print("🤖 System waking up... Ready for training requests.")
    yield
    # 🔴 SHUTDOWN logic (automatic cleanup)
    print("🛑 System shutting down... Force killing vLLM and Env servers.")
    server_manager.stop_servers()

# 2. Initialize FastAPI with the lifespan
app = FastAPI(lifespan=lifespan)




# --- Request Model ---
current_engine = None
current_loaded_model_key = None # Tuple of (model_name, adapter_location)

class InferenceRequest(BaseModel):
    model_name: str
    adapter_location: Optional[str] = None
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7

class TrainRequest(BaseModel):
    model_name: str = "HuggingFaceTB/SmolLM2-135M-Instruct"
    dataset_name: str = "openai/gsm8k"
    output_dir: str = "../../output"
    num_generations: int = 2


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

# --- The Oumi Training Logic ---
def run_oumi_training(req: TrainRequest, clusterID: str):
    #SOLUTIONS: change back to 51216 & set all to localhost 127.0... , hide GPU entirely
    # -------------------------------------------------------------------------
    # 🚨 CRITICAL FIX FOR WSL NETWORKING (IPv4 + Loopback)
    # These must be set BEFORE any other imports
    # -------------------------------------------------------------------------

    # 1. Force IPv4 (Fixes the 0.0.0.0 / ::ffff mismatch on WSL)
    os.environ["NCCL_SOCKET_FAMILY"] = "AF_INET"
    os.environ["GLOO_SOCKET_FAMILY"] = "AF_INET"

    # 2. Force Loopback Interface (Internal Only)
    # This tells PyTorch to ignore your WiFi/Ethernet and talk to itself locally.
    os.environ["GLOO_SOCKET_IFNAME"] = "lo"
    os.environ["NCCL_SOCKET_IFNAME"] = "lo"
    os.environ["TP_SOCKET_IFNAME"] = "lo"

    # 3. Explicit Address Binding
    os.environ["MASTER_ADDR"] = "127.0.0.1"
    os.environ["MASTER_PORT"] = "51216" # We fix the port so it doesn't pick random ones like 51216

    # 4. Distributed Setup (Single GPU Mode)
    os.environ["RANK"] = "0"
    os.environ["WORLD_SIZE"] = "1"
    os.environ["LOCAL_RANK"] = "0"

    # 5. Hardware & Backend
    # We are retrying GPU mode (nccl). If this fails later with OOM, we will switch to gloo/cpu.
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    os.environ["PL_TORCH_DISTRIBUTED_BACKEND"] = "nccl" 

    # 6. Disable Advanced Features (Stability for Consumer GPUs)
    os.environ["NCCL_P2P_DISABLE"] = "1"
    os.environ["NCCL_IB_DISABLE"] = "1"
    os.environ["NCCL_ASYNC_ERROR_HANDLING"] = "1"
    # -------------------------------------------------------------------------    
    print("STARTED... Setting up Oumi training environment.")
    from oumi.train import train
    from oumi.core.configs import (
    TrainingConfig, 
    ModelParams, 
    DataParams,
    TrainingParams,
    TrainerType,DatasetParams,
    DatasetSplitParams,
    GrpoParams
)
    """
    Constructs the Configuration Object and calls oumi.train()
    """
    print("🧠 Building Oumi Configuration Object...")
# -------------------------------------------------------------------------
    # 1. Construct Model Params
    model_config = ModelParams(
        model_name=req.model_name,
        model_max_length=1024,
        torch_dtype_str="bfloat16",
        trust_remote_code=True
    )

    # 2. Construct Data Params
    data_config = DataParams(
          train=DatasetSplitParams(
              datasets=[
                  DatasetParams(  # <--- WRAP IT IN THIS CLASS
                      dataset_name=req.dataset_name, 
                      split="train[:50]" ,#!JUST FOR TESTING , on production use split="train"
                      subset="main"
                  )
              ]
          )
      )
    # 3. Construct Training Params (The Complex Part)
    # Note: We use a dictionary for 'grpo' params as they are often passed via **kwargs in TRL
    training_config = TrainingParams(
        trainer_type=TrainerType.TRL_GRPO,
        # local_rank=-1,
        output_dir=req.output_dir + f"/cluster{clusterID}",
        learning_rate=1e-6,
        reward_functions=["env_reward"],  # Refers to @register in custom_logic.py
        
        # GRPO-Specific Settings mapped into the config
        # grpo={
        #     "use_vllm": True,
        #     "rollout_function": "api_vllm_rollout", # Refers to @register
        #     # "num_generations": req.num_generations,
        #     "max_completion_length": 512,
        # },
        grpo=GrpoParams(
            use_vllm=True,
            # ✅ CHANGE 1: Explicitly point to the new vLLM port (8001)
            # vllm_server_url="http://localhost:8001/v1",
            rollout_function="api_vllm_rollout",
            num_generations=req.num_generations,
            max_completion_length=512,
        ),
        # Standard settings
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        enable_wandb=False
    )

    # 4. Final Config Object
    config = TrainingConfig(
        model=model_config,
        data=data_config,
        training=training_config
    )

    # 5. Run Training (Blocking Call)
    # We set CUDA_VISIBLE_DEVICES to 1 so training doesn't clash with vLLM on GPU 0
    # os.environ["CUDA_VISIBLE_DEVICES"] = ""
    # # os.environ["CUDA_VISIBLE_DEVICES"] = ""
    # # ✅ ADD THESE LINES TO FIX THE TIMEOUT
    # # This forces PyTorch to look at the local loopback explicitly, avoiding WSL routing bugs.
    # os.environ["MASTER_ADDR"] = "127.0.0.1"
    # os.environ["MASTER_PORT"] = "29500"  # Standard PyTorch port
    
    # # Optional: Force "gloo" backend since we are on CPU. 
    # # (NCCL is for GPU, and we disabled GPU for the trainer).
    # os.environ["PL_TORCH_DISTRIBUTED_BACKEND"] = "gloo"
    print("🚀 Calling oumi.train.train() ...")
    try:
        train(config)
        print("✅ Training successfully completed.")
    except Exception as e:
        print(f"❌ Training failed: {e}")
    finally:
        # Cleanup
        server_manager.stop_servers()

# --- Endpoints ---

@app.post("/rl/train/{clusterID}")
async def start_training(req: TrainRequest, background_tasks: BackgroundTasks, clusterID: str):
    # 1. Start Infrastructure (vLLM + Env)
    try:
        # Check if servers are already running to avoid double-start errors
        # (You might want to add a check inside start_servers or here)
        server_manager.start_servers(req.model_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start servers: {e}")

    # 2. Wait for servers to spin up
    # Ideally replace this sleep with a loop checking http://localhost:8000/health
    print("⏳ Waiting for vLLM & Env to initialize...")
    await asyncio.sleep(15) 
    
    print(f"✅ Servers should be up. Starting training for Cluster {clusterID}...")
    
    # 3. Trigger Training in Background
    # Ensure run_oumi_training accepts (req, clusterID) arguments
    background_tasks.add_task(run_oumi_training, req, clusterID)

    return {
        "status": "started", 
        "cluster_id": clusterID,
        "message": "Infrastructure active. Training loop initiated in background."
    }
@app.post("/rl/stop")
def stop_all():
    server_manager.stop_servers()
    return {"status": "stopped"}





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
    from oumi.core.types.conversation import Conversation, Message, Role
    from oumi.core.configs import InferenceConfig, GenerationParams

    engine = get_engine(request.model_name, request.adapter_location)

    # Prepare the input conversation
    input_conversation = Conversation(
        messages=[
            Message(role=Role.USER, content=request.prompt)
        ]
    )

    # Configure generation parameters (max tokens, temp, etc.)
    inference_config = InferenceConfig(
        generation=GenerationParams(
            max_new_tokens=request.max_tokens,
            temperature=request.temperature
        )
    )

    try:
        # Run Inference
        # engine.infer expects a list of conversations and returns a list of conversations
        outputs = engine.infer(
            input=[input_conversation], 
            inference_config=inference_config
        )
        
        # Extract the assistant's response from the last message
        response_text = outputs[0].messages[-1].content
        return {"response": response_text}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference failed: {str(e)}")
