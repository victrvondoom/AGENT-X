import json
import yaml
from pathlib import Path
from oumi import train
from oumi.core.configs import TrainingConfig

#------------------
# TRAIN
#------------------
def removeslash(input_string):
   #remove / and make them _
    return input_string.rstrip('/').replace('/', '_')

def json_to_dict(json_data):
    data_dict = json.loads(json_data)
    return data_dict

def create_folder_pathlib(folder_name):
    # 1. Define the Path object
    new_folder = Path(folder_name)
    
    # 2. Use the .mkdir() method
    try:
        # exist_ok=True prevents an error if the directory already exists
        new_folder.mkdir(exist_ok=True) 
        print(f"Folder '{new_folder}' created successfully (or already exists).")
    except Exception as e:
        print(f"An error occurred: {e}")

def count_files_pathlib(folder_path):
    """Counts files in a folder, excluding subdirectories."""
    
    folder = Path(folder_path)
    
    if not folder.is_dir():
        print(f"Error: '{folder_path}' is not a valid directory.")
        return 0

    # The .glob('*') pattern iterates over all items in the directory.
    # .is_file() filters out any subdirectories.
    file_count = sum(1 for item in folder.glob('*') if item.is_file())
    
    return file_count
# WRITE YAML FILE
def WriteYAMLFile_train(clusterID, datasets, basemodal, folder_path): 
    yamlformat={}
    step=0
    #NOTE: step 2 should have lower learning rate and should use the adpter weight of the previous step as the base weight 
    for dataset in datasets:  
        yamlformat={
    'model':{
    'model_name': basemodal,
    'model_max_length': 2048,
    'torch_dtype_str': "bfloat16",
    'attn_implementation': "sdpa",
    'load_pretrained_weights': True,
    'trust_remote_code': True,
    },
    'data':{
    'train':{
        'datasets':[
        {'dataset_name': dataset}
        ]
    }
    },
    'training':{
    'trainer_type': "TRL_SFT",
    'save_final_model': True,
    'save_steps': 100,
    'max_steps': 10,
    'per_device_train_batch_size': 1,
    'gradient_accumulation_steps': 16,

    'ddp_find_unused_parameters': False,
    'optimizer': "adamw_torch",
    'learning_rate': 2.0e-05,
    'compile': False,
    'dataloader_num_workers': 0,
    'dataloader_prefetch_factor': 32,

    'seed': 192847,
    'use_deterministic': True,

    'logging_steps': 5,
    'log_model_summary': False,
    'empty_device_cache_steps': 50,
    'run_name': removeslash(basemodal)+".step"+str(step),
    'output_dir': f"../../output/cluster{clusterID}/step_"+str(step),
    'include_performance_metrics': True,
    

    'use_peft': True
    },
    'peft':{
    'lora_r': 8,
    'lora_alpha': 16,
    'lora_dropout': 0.05,
    'lora_bias': "none",
    'lora_target_modules':[
        "q_proj",
        "v_proj",
        "k_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj"
    ]
    }
        }
        if step>0:
            yamlformat['model']['adapter_model']=f"../../output/cluster{clusterID}/step_{step-1}/"
            rate=yamlformat['training']['learning_rate']/2
            yamlformat['training']['learning_rate']=rate
        create_folder_pathlib(folder_path)
        file_path = f"{folder_path}/step_{step}.yaml"
        print(file_path)
        with open(file_path, 'w') as yaml_file:
            yaml.dump(yamlformat, yaml_file,Dumper=yaml.SafeDumper, sort_keys=False)
        step+=1

# Train YAML FILE
def TrainYAMLFile(CluterId,yaml_file_path):
    filecount=count_files_pathlib(yaml_file_path)
    for i in range(filecount):
        config = TrainingConfig.from_yaml( f'../../configs/cluster{CluterId}/step_{i}.yaml')
        train(config)

# train,py USAGE EXAMPLE:

# dict_output = json_to_dict(sample_json)
# WriteYAMLFile(dict_output['clusterID'],dict_output['datasets'],dict_output['BaseModel'],f'../configs/cluster{dict_output["clusterID"]}')
# TrainYAMLFile(dict_output['clusterID'], f'../configs/cluster{dict_output["clusterID"]}')



#------------------
# EVAL
#------------------
#read json files

# WRITE YAML FILE
def WriteYAMLFile_eval(modelName,adapterLoc,clusterID): 
    yamlformat={
        'model':{
  'model_name': modelName,
  'model_max_length': 1024,
  'torch_dtype_str': "float16",
  'attn_implementation': "sdpa",
  'load_pretrained_weights': True,
  'trust_remote_code': True,
  'shard_for_eval': True,
        },
'generation':{
  'batch_size': 2
},
'tasks':[
  {
        "evaluation_backend": "lm_harness",
        "task_name": "mmlu",
        "num_samples": 100,
        "eval_kwargs": {
            "num_fewshot": 0 # pure knowledge test
        }
    },
    {
        "evaluation_backend": "lm_harness",
        "task_name": "arc_challenge",
        "eval_kwargs": {
            "num_fewshot":15
        }
    },
    {
        "evaluation_backend": "lm_harness",
        "task_name": "hellaswag",
        "eval_kwargs": {
            "num_fewshot": 5
        }
    }
],
'enable_wandb': False,
'run_name': "eval_cluster"+str(clusterID),
'output_dir': f"../../eval_results/"+str(clusterID),

    }
    if adapterLoc:
            yamlformat['model']['adapter_model']=adapterLoc
    create_folder_pathlib(f'../../configs/eval_cluster{clusterID}')
    with open(f'../../configs/eval_cluster{clusterID}/eval.yaml', 'w') as yaml_file:
        yaml.dump(yamlformat, yaml_file,Dumper=yaml.SafeDumper, sort_keys=False)
    return "Success"
    

# EVAL YAML FILE
def EvalYAMLFile(clusterID):
    from oumi import evaluate
    from oumi.core.configs import EvaluationConfig
    config = EvaluationConfig.from_yaml( f'../../configs/eval_cluster{clusterID}/eval.yaml')
    evaluate(config)
    return "Success"
# eval.py USAGE EXAMPLE:
