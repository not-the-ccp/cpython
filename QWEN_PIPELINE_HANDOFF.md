# Qwen handoff: implement the frozen pipeline-expression experiment

You are in a CPython clone. The target work is already isolated on branch `pipe-topic-3.14.7`, based exactly on upstream CPython `v3.14.7` commit `823f0323ee6ec1402088b73bce1a38473cac36dc`.

## First action

Read **all of `Misc/PIPELINE_EXPERIMENT.md` before editing anything**. That file is the authoritative language-design contract.

Do not redesign the feature. Your role is implementation, regeneration, compilation, testing, and mechanical cleanup.

## Fixed syntax

```python
value |> body_using_$
```

Examples:

```python
"hello" |> len($) |> hex($)
text |> re.sub(pattern, replacement, $)
xs |> map(f, $) |> list($)
obj |> $.method()
args |> f(*$)
kwargs |> f(**$)
```

`x |> f`, `x |> f()`, and a standalone `$` are errors. There is no implicit argument insertion, implicit call, partial creation, overloading hook, second pipe operator, or None-aware variant.

Precedence is fixed: `|>` is lower than `or`, higher than conditional expressions and `lambda`. It is left-associative.

## Required implementation sequence

1. Verify branch/base:

   ```sh
   git status
   git branch --show-current
   git rev-parse HEAD
   git log --oneline --decorate -5
   ```

   Work on `pipe-topic-3.14.7`. Preserve the design-doc commits.

2. Read the relevant CPython source before editing:

   * `Grammar/Tokens`
   * `Grammar/python.gram`
   * `Tools/peg_generator/pegen/parser_generator.py`
   * `Parser/Python.asdl`
   * `Python/ast_preprocess.c`
   * `Python/symtable.c`
   * `Python/codegen.c`
   * `Python/ast_unparse.c`
   * nearby AST validator/conversion code and tests

3. Add exact tokens `VBARGREATER '|>'` and `DOLLAR '$'` through the normal token-generation mechanism.

4. Add AST nodes:

   ```text
   Pipeline(expr value, expr body)
   PipeTopic
   ```

   Do not use `BinOp`.

5. Modify the expression grammar exactly in the semantic position specified by the design document: a left-recursive `pipeline` between conditional/lambda handling and `disjunction`; `$` is an atom yielding `PipeTopic`.

6. Add static validation during AST preprocessing so it also applies to AST-only parsing:

   * `$` must be inside a pipe body.
   * every Pipeline body must use its own topic at least once;
   * nested Pipeline bodies own their own `$`;
   * a nested Pipeline LHS is still evaluated in the outer topic context.

7. Implement symtable support with an active-topic stack/context.

   Use a source-unspellable compiler-generated identifier unique to each `Pipeline` internal AST node. Do **not** put that identifier in the public AST. A helper derived from the AST-node identity/address is acceptable for this experiment if symtable and codegen deterministically obtain the same string for the same node.

   For a Pipeline: visit the LHS in the outer topic context, define the hidden binding in the current Python scope, push it, visit the body, pop it.

   For PipeTopic: add a USE of the current hidden binding.

   Keep the topic context across nested lambda/comprehension symbol-table traversal so Python's ordinary free/cell analysis handles captures.

8. Implement direct code generation. Do not lower through a Python lambda, `functools.partial`, a tuple trick, or a new runtime callable.

   For a Pipeline:

   * compile LHS;
   * STORE to its hidden topic binding using ordinary name-scope machinery;
   * push topic context;
   * compile body, leaving body result as Pipeline result;
   * pop topic context.

   For PipeTopic: LOAD the active hidden binding through ordinary name-scope machinery.

   Evaluation order must be LHS before *any* body evaluation.

9. Add `ast.unparse()` handling and precedence. Round-trip parsing must work.

10. Regenerate generated files. Prefer the normal focused targets if obvious from the Makefile; otherwise use:

    ```sh
    make regen-all
    ```

11. Configure/build a debug interpreter if the clone is not already configured:

    ```sh
    ./configure --with-pydebug
    make -j"$(nproc)"
    ```

12. Implement the complete focused test matrix from `Misc/PIPELINE_EXPERIMENT.md` before broad testing. Do not weaken existing tests.

13. Run at least:

    ```sh
    ./python -m test test_grammar test_ast test_tokenize test_compile test_symtable test_dis
    ```

    Then run the broad suite:

    ```sh
    ./python -m test -j2
    ```

    If full-suite runtime is unreasonable in the harness, run the largest practical standard suite and state exactly what was/was not run. Do not claim a clean suite without evidence.

14. Add an experiment-only Python program exercising mixed stdlib APIs as specified in the design document. Its purpose is data collection; do not change semantics because you personally prefer a prettier spelling.

15. Inspect the diff for generated-file noise, accidental ABI/public-C-API changes, debug prints, weakened tests, and unrelated formatting.

16. Commit implementation in sensible reviewable commits. Do not merge to `main` and do not open an upstream `python/cpython` PR. This is an experimental fork branch.

## Mandatory stop/report conditions

Stop and report, with file/line evidence, rather than redesigning if you find that any fixed semantic requirement would require substantially different architecture than described.

In particular, do not silently change:

* `$` to `_`, `?`, `%`, etc.;
* explicit topic use into implicit first/last injection;
* the precedence;
* nested topic ownership;
* closure/scoping behavior;
* the AST shape;
* evaluation order;
* or the absence of operator overloading.

Compiler-internal helper naming, C function placement, test-file organization, and regen target choice are yours to resolve mechanically by following nearby CPython conventions.

## Completion report

When done, report:

* commits created;
* files changed;
* generated targets run;
* build command/result;
* focused test command/result;
* broad test command/result;
* a short transcript demonstrating the syntax in `./python`;
* `ast.dump()` and `ast.unparse()` examples;
* `dis.dis()` of a short pipeline;
* any hidden-topic artifact visible through `locals()`, `globals()`, `co_varnames`, or tracing;
* any compiler warnings;
* any remaining known implementation limitation.

Do not include language-design recommendations unless asked. The design will be reviewed separately after there is a working implementation and experiment data.