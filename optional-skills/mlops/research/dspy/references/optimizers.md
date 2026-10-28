# DSPy Optimizers (Teleprompters)

Complete guide to DSPy's optimization algorithms for improving prompts and model weights.

## What are Optimizers?

DSPy optimizers (called "teleprompters") automatically improve your modules by:
- **Synthesizing few-shot examples** from training data
- **Proposing better instructions** through search
- **Fine-tuning model weights** (optional)

**Key idea**: Instead of manually tuning prompts, define a metric and let DSPy optimize.

## Optimizer Selection Guide

| Optimizer | Best For | Speed | Quality | Data Needed |
|-----------|----------|-------|---------|-------------|
| BootstrapFewShot | General purpose | Fast | Good | 10-50 examples |
| MIPRO | Instruction tuning | Medium | Excellent | 50-200 examples |
| BootstrapFinetune | Fine-tuning | Slow | Excellent | 100+ examples |
| COPRO | Prompt optimization | Medium | Good | 20-100 examples |
| KNNFewShot | Quick baseline | Very fast | Fair | 10+ examples |

## Core Optimizers

### BootstrapFewShot

**Most popular optimizer** - Generates few-shot demonstrations from training data.

**How it works:**
1. Takes your training examples
2. Uses your module to generate predictions
3. Selects high-quality predictions (based on metric)
4. Uses these as few-shot examples in future prompts

**Parameters:**
- `metric`: Function that scores predictions (required)
- `max_bootstrapped_demos`: Max demonstrations to generate (default: 4)
- `max_labeled_demos`: Max labeled examples to use (default: 16)
- `max_rounds`: Optimization iterations (default: 1)
- `metric_threshold`: Minimum score to accept (optional)

```python
import dspy
from dspy.teleprompt import BootstrapFewShot

# Define metric
def validate_answer(example, pred, trace=None):
    """Return True if prediction matches gold answer."""
    return example.answer.lower() == pred.answer.lower()

# Training data
trainset = [
    dspy.Example(question="What is 2+2?", answer="4").with_inputs("question"),
    dspy.Example(question="What is 3+5?", answer="8").with_inputs("question"),
    dspy.Example(question="What is 10-3?", answer="7").with_inputs("question"),
]

# Create module
qa = dspy.ChainOfThought("question -> answer")

# Optimize
optimizer = BootstrapFewShot(
    metric=validate_answer,
    max_bootstrapped_demos=3,
    max_rounds=2
)

optimized_qa = optimizer.compile(qa, trainset=trainset)

# Now optimized_qa has learned few-shot examples!
result = optimized_qa(question="What is 5+7?")
```

**Best practices:**
- Start with 10-50 training examples
- Use diverse examples covering edge cases
- Set `max_bootstrapped_demos=3-5` for most tasks
- Increase `max_rounds=2-3` for better quality

**When to use:**
- First optimizer to try
- You have 10+ labeled examples
- Want quick improvements
- General-purpose tasks

### MIPRO (Most Important Prompt Optimization)

**State-of-the-art optimizer** - Iteratively searches for better instructions.

**How it works:**
1. Generates candidate instructions
2. Tests each on validation set
3. Selects best-performing instructions
4. Iterates to refine further

**Parameters:**
- `metric`: Evaluation metric (required)
- `num_candidates`: Instructions to try per iteration (default: 10)
- `init_temperature`: Sampling temperature (default: 1.0)
- `verbose`: Show progress (default: False)

```python
from dspy.teleprompt import MIPRO

# Define metric with more nuance
def answer_quality(example, pred, trace=None):
    """Score answer quality 0-1."""
    if example.answer.lower() in pred.answer.lower():
        return 1.0
    # Partial credit for similar answers
    return 0.5 if len(set(example.answer.split()) & set(pred.answer.split())) > 0 else 0.0

# Larger training set (MIPRO benefits from more data)
trainset = [...]  # 50-200 examples
valset = [...]    # 20-50 examples

# Create module
qa = dspy.ChainOfThought("question -> answer")

# Optimize with MIPRO
optimizer = MIPRO(
    metric=answer_quality,
    num_candidates=10,
    init_temperature=1.0,
    verbose=True
)

optimized_qa = optimizer.compile(
    student=qa,
    trainset=trainset,
    valset=valset,  # MIPRO uses separate validation set
    num_trials=100   # More trials = better quality
)
```

**Best practices:**
- Use 50-200 training examples
- Separate validation set (20-50 examples)
- Run 100-200 trials for best results
- Takes 10-30 minutes typically

**When to use:**
- You have 50+ labeled examples
- Want state-of-the-art performance
- Willing to wait for optimization
- Complex reasoning tasks

### BootstrapFinetune

**Fine-tune model weights** - Creates training dataset for fine-tuning.

**How it works:**
1. Generates synthetic training data
2. Exports data in fine-tuning format
3. You fine-tune model separately
4. Load fine-tuned model back

**Parameters:**
- `metric`: Evaluation metric (required)
- `max_bootstrapped_demos`: Demonstrations to generate (default: 4)
- `max_rounds`: Data generation rounds (default: 1)

```python
from dspy.teleprompt import BootstrapFinetune

# Training data
trainset = [...]  # 100+ examples recommended

# Define metric
def validate(example, pred, trace=None):
    return example.answer == pred.answer

# Create module
qa = dspy.ChainOfThought("question -> answer")

# Generate fine-tuning data
optimizer = BootstrapFinetune(metric=validate)
optimized_qa = optimizer.compile(qa, trainset=trainset)

# Exports training data to file
# You then fine-tune using your LM provider's API

# After fine-tuning, load your model:
finetuned_lm = dspy.OpenAI(model="ft:gpt-3.5-turbo:your-model-id")
dspy.settings.configure(lm=finetuned_lm)
```

**Best practices:**
- Use 100+ training examples
- Validate on held-out test set
- Monitor for overfitting
- Compare with prompt-based methods first

**When to use:**
- You have 100+ examples
- Latency is critical (fine-tuned models faster)
- Task is narrow and well-defined
- Prompt optimization isn't enough

### COPRO (Coordinate Prompt Optimization)

**Optimize prompts via gradient-free search.**

**How it works:**
1. Generates prompt variants
2. Evaluates each variant
3. Selects best prompts
4. Iterates to refine

```python
from dspy.teleprompt import COPRO

# Training data
trainset = [...]

# Define metric
def metric(example, pred, trace=None):
    return example.answer == pred.answer

# Create module
qa = dspy.ChainOfThought("question -> answer")

# Optimize with COPRO
optimizer = COPRO(
    metric=metric,
    breadth=10,  # Candidates per iteration
    depth=3      # Optimization rounds
)

optimized_qa = optimizer.compile(qa, trainset=trainset)
```

**When to use:**
- Want prompt optimization
- Have 20-100 examples
- MIPRO too slow

### KNNFewShot

**Simple k-nearest neighbors** - Selects similar examples for each query.

**How it works:**
1. Embeds all training examples
2. For each query, finds k most similar examples
3. Uses these as few-shot demonstrations

```python
from dspy.teleprompt import KNNFewShot

trainset = [...]

# No metric needed - just selects similar examples
optimizer = KNNFewShot(k=3)
optimized_qa = optimizer.compile(qa, trainset=trainset)

# For each query, uses 3 most similar examples from trainset
```

**When to use:**
- Quick baseline
- Have diverse training examples
- Similarity is good proxy for helpfulness

## Writing Metrics

Metrics are functions that score predictions. They're critical for optimization.

### Binary Metrics

```python
def exact_match(example, pred, trace=None):
    """Return True if prediction exactly matches gold."""
    return example.answer == pred.answer

def contains_answer(example, pred, trace=None):
    """Return True if prediction contains gold answer."""
    return example.answer.lower() in pred.answer.lower()
```

### Continuous Metrics

```python
def f1_score(example, pred, trace=None):
    """F1 score between prediction and gold."""
    pred_tokens = set(pred.answer.lower().split())
    gold_tokens = set(example.answer.lower().split())

    if not pred_tokens:
        return 0.0

    precision = len(pred_tokens & gold_tokens) / len(pred_tokens)
    recall = len(pred_tokens & gold_tokens) / len(gold_tokens)

    if precision + recall == 0:
        return 0.0

    return 2 * (precision * recall) / (precision + recall)

def semantic_similarity(example, pred, trace=None):
    """Embedding similarity between prediction and gold."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')

    emb1 = model.encode(example.answer)
    emb2 = model.encode(pred.answer)

    similarity = cosine_similarity(emb1, emb2)
    return similarity
```

### Multi-Factor Metrics

```python
def comprehensive_metric(example, pred, trace=None):
    """Combine multiple factors."""
    score = 0.0

    # Correctness (50%)
    if example.answer.lower() in pred.answer.lower():
        score += 0.5

    # Conciseness (25%)
    if len(pred.answer.split()) <= 20:
        score += 0.25

    # Citation (25%)
    if "source:" in pred.answer.lower():
        score += 0.25

    return score
```

### Using Trace for Debugging

```python
def metric_with_trace(example, pred, trace=None):
    """Metric that uses trace for debugging."""
    is_correct = example.answer == pred.answer

    if trace is not None and not is_correct:
        # Log failures for analysis
        print(f"Failed on: {example.question}")
        print(f"Expected: {example.answer}")
        print(f"Got: {pred.answer}")

    return is_correct
```

## Evaluation Best Practices

### Train/Val/Test Split

```python
# Split data
trainset = data[:100]   # 70%
valset = data[100:120]  # 15%
testset = data[120:]    # 15%

# Optimize on train
optimized = optimizer.compile(module, trainset=trainset)

# Validate during optimization (for MIPRO)
optimized = optimizer.compile(module, trainset=trainset, valset=valset)

# Evaluate on test
from dspy.evaluate import Evaluate
evaluator = Evaluate(devset=testset, metric=metric)
score = evaluator(optimized)
```

### Cross-Validation

```python
from sklearn.model_selection import KFold

kfold = KFold(n_splits=5)
scores = []

for train_idx, val_idx in kfold.split(data):
    trainset = [data[i] for i in train_idx]
    valset = [data[i] for i in val_idx]

    optimized = optimizer.compile(module, trainset=trainset)
    score = evaluator(optimized, devset=valset)
    scores.append(score)

print(f"Average score: {sum(scores) / len(scores):.2f}")
```

### Comparing Optimizers

This comparison and the Optimization Workflow below target **DSPy 3.4.1**
(release tag `3.4.1`, commit `099982b3c3eec9fc4ec4796d49915af42367a253`); the
interfaces were checked against that source, not by running it. They are independent examples,
not a claim that the other, legacy snippets on this page have been migrated.
Prerequisites: an already configured LM, a compatible `module`, a declared
`metric`, and nonempty, disjoint `trainset`, `valset`, and untouched `testset`.
MIPROv2 also requires DSPy's optional `optuna` dependency. Running optimization
can call the configured LM; this guide does not grant provider or cost approval.

Use development scores for comparisons that may guide a choice. This block only
prints scores; it does not itself select a winner. The validation set is reused
for tuning and comparison, so its scores are not unbiased held-out estimates.
`Evaluate(...).score` is a percentage (0–100 for metrics in [0, 1]).

```python
from dspy.evaluate import Evaluate
from dspy.teleprompt import BootstrapFewShot, MIPROv2

evaluator = Evaluate(devset=valset, metric=metric)
results = {}

for opt_name, optimizer in [
    ("baseline", None),
    ("fewshot", BootstrapFewShot(metric=metric)),
    ("mipro_v2", MIPROv2(metric=metric, auto="light")),
]:
    if optimizer is None:
        module_opt = module
    elif opt_name == "mipro_v2":
        module_opt = optimizer.compile(module, trainset=trainset, valset=valset)
    else:
        # BootstrapFewShot.compile does not accept valset in this version.
        module_opt = optimizer.compile(module, trainset=trainset)

    score = evaluator(module_opt, devset=valset).score
    results[opt_name] = score

print(results)  # Development percentages from your run, not benchmark claims.
```

## Advanced Patterns

### Custom Optimizer

```python
from dspy.teleprompt import Teleprompter

class CustomOptimizer(Teleprompter):
    def __init__(self, metric):
        self.metric = metric

    def compile(self, student, trainset, **kwargs):
        # Your optimization logic here
        # Return optimized student module
        return student
```

### Multi-Stage Optimization

```python
# Stage 1: Bootstrap few-shot
stage1 = BootstrapFewShot(metric=metric, max_bootstrapped_demos=3)
optimized1 = stage1.compile(module, trainset=trainset)

# Stage 2: Instruction tuning
stage2 = MIPRO(metric=metric, num_candidates=10)
optimized2 = stage2.compile(optimized1, trainset=trainset, valset=valset)

# Final optimized module
final_module = optimized2
```

### Ensemble Optimization

```python
class EnsembleModule(dspy.Module):
    def __init__(self, modules):
        super().__init__()
        self.modules = modules

    def forward(self, question):
        predictions = [m(question=question).answer for m in self.modules]
        # Vote or average
        return dspy.Prediction(answer=max(set(predictions), key=predictions.count))

# Optimize multiple modules
opt1 = BootstrapFewShot(metric=metric).compile(module, trainset=trainset)
opt2 = MIPRO(metric=metric).compile(module, trainset=trainset)
opt3 = COPRO(metric=metric).compile(module, trainset=trainset)

# Ensemble
ensemble = EnsembleModule([opt1, opt2, opt3])
```

## Optimization Workflow

This is a separate example for the **same pinned DSPy source ref** and uses
 the prerequisites declared in
Comparing Optimizers. Use only train/dev data to tune prompts, metrics and
preprocessing. Here the baseline is a reference for reporting improvement;
the predeclared selection compares only the two optimized candidates, keeps
few-shot on a tie, and does not promise either beats the baseline. If you want
the baseline to be eligible, declare that selection rule before seeing test data.
Development-score differences below are **percentage points**, not relative gains.

### 1. Start with Baseline

```python
import dspy
from dspy.evaluate import Evaluate
from dspy.teleprompt import BootstrapFewShot, MIPROv2

evaluator = Evaluate(devset=valset, metric=metric)
baseline = dspy.ChainOfThought("question -> answer")
baseline_score = evaluator(baseline, devset=valset).score
print(f"Baseline (dev): {baseline_score:.2f}")
```

### 2. Try BootstrapFewShot

```python
fewshot = BootstrapFewShot(metric=metric, max_bootstrapped_demos=3)
optimized = fewshot.compile(baseline, trainset=trainset)
fewshot_score = evaluator(optimized, devset=valset).score
print(f"Few-shot (dev): {fewshot_score:.2f} ({fewshot_score - baseline_score:+.2f} pp)")
```

### 3. Try MIPROv2

```python
# auto="light" sets the search budget; do not also set num_candidates/num_trials.
mipro = MIPROv2(metric=metric, auto="light")
optimized_mipro = mipro.compile(baseline, trainset=trainset, valset=valset)
mipro_score = evaluator(optimized_mipro, devset=valset).score
print(f"MIPROv2 (dev): {mipro_score:.2f} ({mipro_score - baseline_score:+.2f} pp)")
```

### 4. Save Best Model

```python
from pathlib import Path

if mipro_score > fewshot_score:
    best = optimized_mipro
else:
    best = optimized
Path("models").mkdir(exist_ok=True)
best.save("models/best_model.json")
```

### 5. Report Once on Untouched Test Data

Freeze the choice above, preprocessing and metric before this step. Evaluate
only that chosen program once on the untouched test set and report the result
separately. Do not feed this result back into model selection or retuning; if you
do, it becomes development feedback and a new independent test set is needed.
This example demonstrates a protocol, not measured performance or production admission.

```python
test_score = evaluator(best, devset=testset).score
print(f"Frozen candidate (test): {test_score:.2f}")
```

## Common Pitfalls

### 1. Overfitting to Training Data

```python
# A larger bootstrapped-demo cap to compare on development data; not proof of overfitting.
optimizer = BootstrapFewShot(max_bootstrapped_demos=20)

# An integer starting point; try values in the range 3 to 5 on development data.
# This caps bootstrapped demos, not all demos; max_labeled_demos is separate.
# Neither this value nor a larger cap guarantees quality or prevents overfitting.
optimizer = BootstrapFewShot(max_bootstrapped_demos=3)
```

### 2. Metric Doesn't Match Task

```python
# ❌ Bad: Binary metric for nuanced task
def bad_metric(example, pred, trace=None):
    return example.answer == pred.answer  # Too strict!

# ✅ Good: Graded metric
def good_metric(example, pred, trace=None):
    # Uses the object-based helper above; it assumes nonempty gold answers.
    return f1_score(example, pred, trace)
```

### 3. Insufficient Training Data

```python
# ❌ Bad: Too little data
trainset = data[:5]  # Not enough!

# ✅ Good: Sufficient data
trainset = data[:50]  # Better
```

### 4. No Validation Set

```python
# ❌ Bad: Optimizing on test set
optimizer.compile(module, trainset=testset)  # Cheating!

# ✅ Good: Proper splits
optimizer.compile(module, trainset=trainset, valset=valset)
evaluator(optimized, devset=testset)
```

## Performance Tips

1. **Start simple**: BootstrapFewShot first
2. **Use representative data**: Cover edge cases
3. **Monitor overfitting**: Validate on held-out set
4. **Iterate metrics**: Refine based on failures
5. **Save checkpoints**: Don't lose progress
6. **Compare to baseline**: Measure improvement
7. **Test multiple optimizers**: Find best fit

## Resources

- **Paper**: "DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines"
- **GitHub**: https://github.com/stanfordnlp/dspy
- **Discord**: https://discord.gg/XCGy2WDCQB
