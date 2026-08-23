import json
import os
from pathlib import Path
from typing import Dict, List, Any, Set
from datetime import datetime


def process_task_result(task_result_data: Dict[str, Any], file_path: str):
    """
    Process the task result JSON data for frontend display.
    
    Args:
        task_result_data: The parsed JSON data from task_result.json
        file_path: The full path to the task_result.json file
    
    Returns:
        Processed data dictionary optimized for frontend consumption
    """
    # Extract metadata
    start_time = task_result_data.get('start_time', 'Unknown')
    duration_sec = task_result_data.get('duration_sec', 0)
    
    # Format timestamp for frontend
    try:
        dt = datetime.strptime(start_time, "%Y%m%d_%H%M%S")
        formatted_time = dt.strftime("%B %d, %Y at %I:%M %p")
    except:
        formatted_time = start_time
    
    # Process results
    results = task_result_data.get('results', {})
    groups = task_result_data.get('groups', {})
    
    # Extract main benchmark name
    benchmark_name = list(results.keys())[0].split('_')[0].upper() if results else "Unknown"
    
    # Build a set of all tasks that belong to groups (these are individual tasks)
    tasks_in_groups: Set[str] = set()
    for group_name, group_members in groups.items():
        tasks_in_groups.update(group_members)
    
    # Separate tasks into categories
    main_score = None
    category_scores = []
    individual_tasks = []
    
    for task_name, metrics in results.items():
        # Extract score
        score = None
        if 'acc_norm,none' in metrics:
            score = metrics['acc_norm,none']
        elif 'acc,none' in metrics:
            score = metrics['acc,none']
        
        if score is None:
            continue
            
        score_percent = round(score * 100, 2)
        alias = metrics.get('alias', task_name).strip()
        
        # Determine task type:
        # 1. Main benchmark score: task_name is a key in groups (it's a parent group)
        # 2. Category score: task_name is in groups AND has children
        # 3. Individual task: task_name is in tasks_in_groups (it's a child of a group)
        
        if task_name in groups:
            # This is a group/category with children
            group_children = groups[task_name]
            if len(group_children) > 0 and task_name != benchmark_name.lower():
                # This is a category
                category_name = alias.replace(' - ', '').replace('_', ' ').strip().title()
                category_scores.append({
                    'name': category_name,
                    'score': score_percent,
                    'color': get_score_color(score_percent)
                })
            elif task_name == benchmark_name.lower() or main_score is None:
                # This is the main benchmark score
                main_score = {
                    'name': benchmark_name,
                    'score': score_percent,
                    'color': get_score_color(score_percent),
                    'is_primary': True
                }
        elif task_name in tasks_in_groups:
            # This is an individual task (leaf node)
            task_display_name = alias.replace('  - ', '').replace(' - ', '').replace('_', ' ').strip().title()
            individual_tasks.append({
                'name': task_display_name,
                'score': score_percent,
                'grade': get_grade(score_percent),
                'color': get_score_color(score_percent)
            })
        elif main_score is None:
            # Fallback: first entry without group info becomes main score
            main_score = {
                'name': benchmark_name,
                'score': score_percent,
                'color': get_score_color(score_percent),
                'is_primary': True
            }
    
    # Combine scores
    overall_scores = []
    if main_score:
        overall_scores.append(main_score)
    overall_scores.extend(category_scores)
    
    # Sort individual tasks by score (descending)
    individual_tasks.sort(key=lambda x: x['score'], reverse=True)
    
    # Calculate statistics
    all_scores = [item['score'] for item in individual_tasks]
    stats = {
        'average': round(sum(all_scores) / len(all_scores), 2) if all_scores else 0,
        'highest': max(all_scores) if all_scores else 0,
        'lowest': min(all_scores) if all_scores else 0,
        'total_tasks': len(all_scores)
    }
    
    return {
        'benchmark_name': benchmark_name,
        'model_name': extract_model_name(file_path),
        'evaluation_date': formatted_time,
        'duration': format_duration(duration_sec),
        'overall_scores': overall_scores,
        'individual_tasks': individual_tasks,
        'statistics': stats,
        'raw_file_path': file_path
    }


def extract_model_name(file_path: str):
    """Extract model name from file path."""
    parent_dir = os.path.basename(os.path.dirname(file_path))
    if parent_dir.startswith('lm_harness_'):
        return parent_dir.replace('lm_harness_', 'Model_')
    return parent_dir


def format_duration(seconds: int):
    """Format duration for frontend display."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def get_score_color(score: float):
    """Assign color based on score for frontend visualization."""
    if score >= 80:
        return "#10b981"
    elif score >= 60:
        return "#3b82f6"
    elif score >= 40:
        return "#f59e0b"
    elif score >= 25:
        return "#ef4444"
    else:
        return "#7f1d1d"


def get_grade(score: float):
    """Assign letter grade based on score."""
    if score >= 90:
        return "A+"
    elif score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"


def generate_json_output(all_results: List[Dict[str, Any]], output_file: str = "model_performance.json"):
    """Generate JSON output for frontend consumption."""
    output_data = {
        'total_evaluations': len(all_results),
        'generated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'evaluations': all_results
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ JSON output saved to: {output_file}")


def print_frontend_preview(processed_data: Dict[str, Any]):
    """Print a preview of how the data will look in the frontend."""
    print(f"\n{'='*80}")
    print(f"MODEL EVALUATION RESULTS")
    print(f"{'='*80}")
    print(f"Model: {processed_data['model_name']}")
    print(f"Benchmark: {processed_data['benchmark_name']}")
    print(f"Evaluated: {processed_data['evaluation_date']}")
    print(f"Duration: {processed_data['duration']}")
    print(f"{'='*80}")
    
    print(f"\n📊 OVERALL PERFORMANCE")
    print(f"{'-'*80}")
    for score_item in processed_data['overall_scores']:
        name = score_item['name']
        score = score_item['score']
        is_primary = score_item.get('is_primary', False)
        prefix = "🎯" if is_primary else "  "
        print(f"{prefix} {name:<30} {score:>6.2f}%")
    
    stats = processed_data['statistics']
    print(f"\n📈 STATISTICS")
    print(f"{'-'*80}")
    print(f"   Total Tasks: {stats['total_tasks']}")
    print(f"   Average Score: {stats['average']:.2f}%")
    print(f"   Highest Score: {stats['highest']:.2f}%")
    print(f"   Lowest Score: {stats['lowest']:.2f}%")
    
    if processed_data['individual_tasks']:
        print(f"\n🏆 TOP 10 PERFORMING TASKS")
        print(f"{'-'*80}")
        print(f"{'Task Name':<50} {'Score':>10} {'Grade':>8}")
        print(f"{'-'*80}")
        
        for task in processed_data['individual_tasks'][:10]:
            name = task['name'][:48] + "..." if len(task['name']) > 48 else task['name']
            print(f"{name:<50} {task['score']:>9.2f}% {task['grade']:>8}")
        
        if len(processed_data['individual_tasks']) > 10:
            print(f"\n⚠️  BOTTOM 5 PERFORMING TASKS")
            print(f"{'-'*80}")
            print(f"{'Task Name':<50} {'Score':>10} {'Grade':>8}")
            print(f"{'-'*80}")
            
            for task in processed_data['individual_tasks'][-5:]:
                name = task['name'][:48] + "..." if len(task['name']) > 48 else task['name']
                print(f"{name:<50} {task['score']:>9.2f}% {task['grade']:>8}")


def read_task_results(root_dir: str):
    """Read all task_result.json files from the directory structure."""
    root_path = Path(root_dir)
    all_processed_results = []
    
    task_result_files = list(root_path.rglob("task_result.json"))
    
    if not task_result_files:
        print(f"⚠️  No task_result.json files found in {root_dir}")
        return []
    
    print(f"\n🔍 Found {len(task_result_files)} evaluation result(s)")
    
    for task_result_file in task_result_files:
        try:
            with open(task_result_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                processed_data = process_task_result(data, str(task_result_file))
                all_processed_results.append(processed_data)
                print_frontend_preview(processed_data)
                
        except json.JSONDecodeError as e:
            print(f"\n⚠️  Error decoding JSON from {task_result_file}: {e}")
        except Exception as e:
            print(f"\n⚠️  Error reading {task_result_file}: {e}")
    
    return all_processed_results


def process_cluster_results(cluster_id: str, base_path: str = "../../eval_results"):
    """Process evaluation results for a specific cluster ID."""
    root_directory = os.path.normpath(os.path.join(base_path, cluster_id))
    
    if not os.path.exists(root_directory):
        print(f"⚠️  Directory '{root_directory}' not found!")
        return []
    
    print(f"\n{'='*80}")
    print(f"AI MODEL PERFORMANCE EVALUATION PROCESSOR")
    print(f"{'='*80}")
    print(f"Cluster ID: {cluster_id}")
    print(f"Path: {root_directory}")
    
    all_results = read_task_results(root_directory)
    
    if all_results:
        output_filename = f"model_performance_{cluster_id}.json"
        generate_json_output(all_results, output_filename)
        
        print(f"\n{'='*80}")
        print(f"✅ Processing Complete!")
        print(f"   Cluster ID: {cluster_id}")
        print(f"   Total Models Evaluated: {len(all_results)}")
        print(f"   Output File: {output_filename}")
        print(f"{'='*80}\n")
    
    return all_results
##DUMMY RESULTANT DATA
'''
{
  "total_evaluations": 1,
  "generated_at": "2024-12-12 10:30:45",
  "evaluations": [
    {
      "benchmark_name": "MMLU",
      "model_name": "Model_20251211_170658",
      "evaluation_date": "December 11, 2024 at 05:06 PM",
      "duration": "21m 18s",
      "overall_scores": [
        {
          "name": "MMLU",
          "score": 23.23,
          "color": "#ef4444",
          "is_primary": true
        },
        {
          "name": "Humanities",
          "score": 25.08,
          "color": "#ef4444"
        },
        {
          "name": "Other",
          "score": 23.08,
          "color": "#ef4444"
        },
        {
          "name": "Social Sciences",
          "score": 22.5,
          "color": "#ef4444"
        },
        {
          "name": "Stem",
          "score": 22.53,
          "color": "#ef4444"
        }
      ],
      "individual_tasks": [
        {
          "name": "World Religions",
          "score": 41.0,
          "grade": "D",
          "color": "#f59e0b"
        },
        {
          "name": "Marketing",
          "score": 34.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Conceptual Physics",
          "score": 33.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Machine Learning",
          "score": 32.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School World History",
          "score": 31.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Business Ethics",
          "score": 30.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Human Aging",
          "score": 30.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Medical Genetics",
          "score": 30.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Sociology",
          "score": 30.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Computer Security",
          "score": 28.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Jurisprudence",
          "score": 28.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Us Foreign Policy",
          "score": 28.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Econometrics",
          "score": 27.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Electrical Engineering",
          "score": 27.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Us History",
          "score": 27.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Professional Psychology",
          "score": 27.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Virology",
          "score": 26.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Abstract Algebra",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "College Computer Science",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Elementary Mathematics",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Formal Logic",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Computer Science",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School European History",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "International Law",
          "score": 25.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "College Biology",
          "score": 24.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Human Sexuality",
          "score": 24.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Security Studies",
          "score": 24.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Moral Disputes",
          "score": 23.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Prehistory",
          "score": 23.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Psychology",
          "score": 22.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Moral Scenarios",
          "score": 22.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Professional Law",
          "score": 22.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Mathematics",
          "score": 22.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Physics",
          "score": 22.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Professional Accounting",
          "score": 22.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "College Mathematics",
          "score": 21.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "College Physics",
          "score": 21.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Microeconomics",
          "score": 21.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Nutrition",
          "score": 21.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Public Relations",
          "score": 21.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "College Chemistry",
          "score": 20.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "College Medicine",
          "score": 20.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Miscellaneous",
          "score": 20.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Logical Fallacies",
          "score": 19.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Astronomy",
          "score": 18.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Global Facts",
          "score": 18.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Chemistry",
          "score": 18.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Management",
          "score": 18.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Anatomy",
          "score": 17.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Clinical Knowledge",
          "score": 16.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Geography",
          "score": 16.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Government And Politics",
          "score": 16.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Statistics",
          "score": 16.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Philosophy",
          "score": 15.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "Professional Medicine",
          "score": 15.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Macroeconomics",
          "score": 14.0,
          "grade": "F",
          "color": "#ef4444"
        },
        {
          "name": "High School Biology",
          "score": 12.0,
          "grade": "F",
          "color": "#7f1d1d"
        }
      ],
      "statistics": {
        "average": 23.23,
        "highest": 41.0,
        "lowest": 12.0,
        "total_tasks": 57
      },
      "raw_file_path": "../../eval_results/11122025122303/lm_harness_20251211_170658/task_result.json"
    }
  ]
}

'''