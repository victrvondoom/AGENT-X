# ============================================================================
# FILE: apis/utils/grpo_trainer.py
# Core GRPO training logic
# ============================================================================

import os
import torch
from typing import Optional

from oumi.train import train
from oumi.core.configs import (
    TrainingConfig,
    ModelParams,
    DataParams,
    TrainingParams,
    TrainerType,
    DatasetParams,
    DatasetSplitParams,
    GrpoParams,
)
from oumi.core.registry import register, RegistryType


# ============================================================================
# REWARD FUNCTION (Register once)
# ============================================================================

@register("simple_reward", RegistryType.REWARD_FUNCTION)
def simple_reward(completions, **kwargs):
    """
    Simple reward function based on response length.
    
    In production, replace this with:
    - Your actual reward model
    - API call to your environment
    - LLM-as-judge scoring
    """
    rewards = []
    for completion in completions:
        # Simple heuristic: reward longer, more detailed responses
        words = len(completion.split())
        # Normalize to [0, 1] range
        reward = min(1.0, words / 100.0)
        rewards.append(reward)
    
    return rewards


# ============================================================================
# ROLLOUT FUNCTION (Register once)
# ============================================================================

@register("local_rollout", RegistryType.ROLLOUT_FUNCTION)
def local_rollout(prompts, args, processing_class):
    """
    Local rollout function that generates completions locally.
    Works without vLLM or external servers.
    """
    from transformers import AutoModelForCausalLM, AutoTokenizer
    
    # Load model and tokenizer
    model = AutoModelForCausalLM.from_pretrained(
        "HuggingFaceTB/SmolLM2-135M-Instruct",
        torch_dtype=torch.float16,
        device_map="cuda",
    )
    tokenizer = AutoTokenizer.from_pretrained("HuggingFaceTB/SmolLM2-135M-Instruct")
    
    completions = []
    
    for prompt in prompts:
        # Tokenize prompt
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
        
        # Generate completions
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=args.max_completion_length,
                num_return_sequences=args.num_generations,
                temperature=getattr(args, "temperature", 0.7),
                top_p=0.9,
                do_sample=True,
            )
        
        # Decode
        for output in outputs:
            completion = tokenizer.decode(output, skip_special_tokens=True)
            completion = completion[len(prompt):]  # Remove prompt from output
            completions.append(completion)
    
    return {"completions": completions}


# ============================================================================
# MAIN TRAINER CLASS
# ============================================================================

class GrpoTrainer:
    """GRPO Training with Oumi"""
    
    @staticmethod
    def train(
        model_name: str,
        
        dataset_name: str,
        output_dir: str,
        num_epochs: int = 1,
        batch_size: int = 2,
        learning_rate: float = 1e-6,
        max_completion_length: int = 256,
    ):
        """
        Run GRPO training.
        
        Args:
            model_name: HuggingFace model ID
            adapter_location: Optional LoRA adapter path
            dataset_name: HuggingFace dataset
            output_dir: Output directory
            num_epochs: Number of epochs
            batch_size: Batch size (must be even for num_generations=2)
            learning_rate: Learning rate
            max_completion_length: Max completion tokens
        """
        
        # ====================================================================
        # STEP 1: Environment Setup
        # ====================================================================
        print("\n" + "=" * 80)
        print("GRPO TRAINING SETUP")
        print("=" * 80)
        
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"
        os.environ.pop("RANK", None)
        os.environ.pop("LOCAL_RANK", None)
        os.environ.pop("WORLD_SIZE", None)
        os.environ.pop("MASTER_ADDR", None)
        os.environ.pop("MASTER_PORT", None)
        
        print("\n✅ Environment configured for single GPU")
        print(f"   GPU: {torch.cuda.get_device_name(0)}")
        print(f"   VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
        
        # ====================================================================
        # STEP 2: Model Configuration
        # ====================================================================
        print("\n🧠 Configuring Model...")
        model_config = ModelParams(
            model_name=model_name,
            # adapter_model=adapter_location,  # Load adapter if provided
            model_max_length=512,
            torch_dtype_str="float16",
            trust_remote_code=True,
        )
        print(f"   Model: {model_name}")
        print(f"   Context: 512 tokens")
        
        # ====================================================================
        # STEP 3: Data Configuration
        # ====================================================================
        print("\n📊 Configuring Dataset...")
        data_config = DataParams(
            train=DatasetSplitParams(
                datasets=[
                    DatasetParams(
                        dataset_name=dataset_name,
                        split="train[:20]",  # 20 examples for testing
                        subset="main",
                    )
                ]
            )
        )
        print(f"   Dataset: {dataset_name}")
        print(f"   Examples: 20 (for testing)")
        
        # ====================================================================
        # STEP 4: Training Configuration
        # ====================================================================
        print("\n⚙️  Configuring Training...")
        
        # GRPO requires:
        # - num_generations >= 2 (for advantage calculation)
        # - batch_size divisible by num_generations
        num_generations = 2
        
        training_config = TrainingParams(
            trainer_type=TrainerType.TRL_GRPO,
            output_dir=output_dir,
            learning_rate=learning_rate,
            reward_functions=["simple_reward"],
            
            # Batch settings
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=1,
            
            # GRPO settings
            grpo=GrpoParams(
                use_vllm=False,  # No vLLM - use local generation
                rollout_function="local_rollout",
                num_generations=num_generations,
                max_completion_length=max_completion_length,
                temperature=0.7,
            ),
            
            # Training settings
            num_train_epochs=num_epochs,
            enable_wandb=False,
        )
        
        print(f"   Trainer: TRL_GRPO")
        print(f"   Batch size: {batch_size}")
        print(f"   Num generations: {num_generations}")
        print(f"   Learning rate: {learning_rate}")
        print(f"   Max completion length: {max_completion_length}")
        print(f"   Epochs: {num_epochs}")
        print(f"   Reward function: simple_reward")
        
        # ====================================================================
        # STEP 5: Create Config and Train
        # ====================================================================
        config = TrainingConfig(
            model=model_config,
            data=data_config,
            training=training_config,
        )
        
        print("\n" + "=" * 80)
        print("🚀 STARTING GRPO TRAINING")
        print("=" * 80 + "\n")
        
        try:
            # Run training
            train(config)
            
            print("\n" + "=" * 80)
            print("✅ TRAINING COMPLETED SUCCESSFULLY!")
            print("=" * 80)
            print(f"📁 Output saved to: {output_dir}")
            
        except Exception as e:
            print("\n" + "=" * 80)
            print(f"❌ TRAINING FAILED: {e}")
            print("=" * 80)
            import traceback
            traceback.print_exc()