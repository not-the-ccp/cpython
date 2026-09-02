"""Tests for the pipeline expression (topic pipeline) experiment.

This exercises the frozen design documented in Misc/PIPELINE_EXPERIMENT.md.
The operator is a low-precedence, left-associative ``value |> body`` where
``$`` is the pipeline topic bound to the left-hand value.  The body must
reference ``$``; there is no implicit call or argument injection.
"""

import ast
import asyncio
import copy
import dis
import io
import re
import token
import tokenize
import unittest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_expr(source):
    return ast.parse(source, mode="eval").body


class PipelineTestCase(unittest.TestCase):
    def assertSyntaxError(self, source, message, mode="exec"):
        with self.assertRaises(SyntaxError) as cm:
            compile(source, "<test>", mode)
        self.assertIn(message, cm.exception.msg, str(cm.exception))


# ---------------------------------------------------------------------------
# Tokenize
# ---------------------------------------------------------------------------

class TokenizeTests(PipelineTestCase):
    def tokenize(self, source):
        return list(tokenize.generate_tokens(io.StringIO(source).readline))

    def test_op_types_and_exact_types(self):
        toks = self.tokenize("x = a |> f($)\n")
        self.assertEqual(token.EXACT_TOKEN_TYPES["|>"], token.VBARGREATER)
        self.assertEqual(token.EXACT_TOKEN_TYPES["$"], token.DOLLAR)

        pipe = [t for t in toks if t.string == "|>"][0]
        topic = [t for t in toks if t.string == "$"][0]
        self.assertEqual(pipe.type, token.OP)
        self.assertEqual(pipe.exact_type, token.VBARGREATER)
        self.assertEqual(topic.type, token.OP)
        self.assertEqual(topic.exact_type, token.DOLLAR)

    def test_dollar_is_not_part_of_a_number_or_name(self):
        # '$' is its own operator token, not a NAME character and not a
        # numeric separator.  "a$ b" is two names with a topic between them.
        with self.assertRaises(SyntaxError):
            compile("a$ = 1", "<test>", "exec")

    def test_comments_and_newlines_around_pipe(self):
        toks = self.tokenize("x = a |>  # trailing comment\n"
                             "      f($)  # another\n")
        strings = [t.string for t in toks]
        self.assertIn("|>", strings)
        self.assertIn("$", strings)

    def test_fstring_replacement_field_topic(self):
        # The topic is a valid expression inside an f-string field.
        tree = parse_expr('name |> f"hello, {$}!"')
        self.assertIsInstance(tree, ast.Pipeline)
        body = tree.body
        self.assertIsInstance(body, ast.JoinedStr)
        fv = [v for v in body.values if isinstance(v, ast.FormattedValue)]
        self.assertEqual(len(fv), 1)
        self.assertIsInstance(fv[0].value, ast.PipeTopic)

    def test_untokenize_roundtrip(self):
        source = "x = a |> f($)   # keep me\n"
        toks = self.tokenize(source)
        untokenized = tokenize.untokenize(toks)
        # Re-tokenizing the untokenized text yields the same token strings.
        re_toks = self.tokenize(untokenized)
        a = [t.string for t in toks if t.type not in (tokenize.COMMENT,
                                                       tokenize.NL,
                                                       tokenize.NEWLINE)]
        b = [t.string for t in re_toks if t.type not in (tokenize.COMMENT,
                                                          tokenize.NL,
                                                          tokenize.NEWLINE)]
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

class ASTTests(PipelineTestCase):
    def test_dump_contains_pipeline_and_pipetopic(self):
        tree = parse_expr("x |> f($) |> g($)")
        dumped = ast.dump(tree)
        self.assertIn("Pipeline", dumped)
        self.assertIn("PipeTopic", dumped)

    def test_nodes_are_not_binop(self):
        # The pipe is modeled with dedicated nodes, not a BinOp.
        tree = parse_expr("x |> f($)")
        self.assertIsInstance(tree, ast.Pipeline)
        self.assertNotIsInstance(tree, ast.BinOp)
        self.assertIsInstance(tree.body.args[0], ast.PipeTopic)

    def test_left_associative_nesting(self):
        tree = parse_expr("a |> f($) |> g($)")
        self.assertIsInstance(tree, ast.Pipeline)
        self.assertIsInstance(tree.value, ast.Pipeline)
        self.assertIsInstance(tree.body, ast.Call)

    def test_value_and_body_fields(self):
        tree = parse_expr("lhs |> rhs($)")
        self.assertIsInstance(tree.value, ast.Name)
        self.assertEqual(tree.value.id, "lhs")
        self.assertIsInstance(tree.body, ast.Call)

    def test_unparse_roundtrip(self):
        cases = [
            "x |> f($)",
            "a |> f($) |> g($)",
            "x or y |> f($)",
            "x |> f($) + 1",
            "x |> f($) if c else y",
            "x |> (f($) if c else g($))",
            "(x if c else y) |> f($)",
            "lambda x: x |> f($)",
            "x |> (lambda: $)",
            "x |> ($, $, f($))",
        ]
        for source in cases:
            with self.subTest(source=source):
                tree = ast.parse(source, mode="eval")
                out = ast.unparse(tree)
                re_tree = ast.parse(out, mode="eval")
                self.assertEqual(ast.dump(tree), ast.dump(re_tree))

    def test_manually_constructed_valid_ast_compiles(self):
        topic = ast.PipeTopic()
        topic.lineno = topic.col_offset = 1
        topic.end_lineno = topic.end_col_offset = 1
        value = ast.Name(id="v", ctx=ast.Load())
        value.lineno = value.col_offset = 1
        value.end_lineno = value.end_col_offset = 1
        func = ast.Name(id="f", ctx=ast.Load())
        func.lineno = func.col_offset = 1
        func.end_lineno = func.end_col_offset = 1
        body = ast.Call(func=func, args=[topic], keywords=[])
        body.lineno = body.col_offset = 1
        body.end_lineno = body.end_col_offset = 1
        pipe = ast.Pipeline(value=value, body=body)
        pipe.lineno = pipe.col_offset = 1
        pipe.end_lineno = pipe.end_col_offset = 1
        module = ast.Module(body=[ast.Expr(value=pipe)], type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<manual>", "exec")
        ns = {"v": 4, "f": lambda x: x * 10}
        exec(code, ns)

    def test_manually_constructed_invalid_topic_rejected(self):
        # A PipeTopic that is not inside a Pipeline body is rejected.
        topic = ast.PipeTopic()
        expr = ast.Expr(value=topic)
        module = ast.Module(body=[expr], type_ignores=[])
        ast.fix_missing_locations(module)
        with self.assertRaises((SyntaxError, ValueError)):
            compile(module, "<manual>", "exec")

    def test_manually_constructed_topic_free_body_rejected(self):
        value = ast.Name(id="v", ctx=ast.Load())
        body = ast.Name(id="w", ctx=ast.Load())
        for node in (value, body):
            node.lineno = node.col_offset = 1
            node.end_lineno = node.end_col_offset = 1
        pipe = ast.Pipeline(value=value, body=body)
        pipe.lineno = pipe.col_offset = 1
        pipe.end_lineno = pipe.end_col_offset = 1
        module = ast.Module(body=[ast.Expr(value=pipe)], type_ignores=[])
        ast.fix_missing_locations(module)
        with self.assertRaises((SyntaxError, ValueError)):
            compile(module, "<manual>", "exec")

    def test_copy_roundtrip(self):
        tree = parse_expr("(x |> f($)) if c else (g |> h($))")
        clone = copy.deepcopy(tree)
        self.assertEqual(ast.dump(tree), ast.dump(clone))


# ---------------------------------------------------------------------------
# Syntax errors
# ---------------------------------------------------------------------------

class SyntaxErrorTests(PipelineTestCase):
    def test_dollar_alone(self):
        self.assertSyntaxError("$",
                               "pipeline topic '$' is only valid in a pipeline body")

    def test_topic_free_body_call(self):
        self.assertSyntaxError("x |> f()",
                               "pipeline body must reference '$'")

    def test_topic_free_body_bare(self):
        self.assertSyntaxError("x |> f",
                               "pipeline body must reference '$'")

    def test_assignment_to_dollar(self):
        self.assertSyntaxError("$ = 5", "assign")

    def test_del_dollar(self):
        self.assertSyntaxError("del $", "delete")

    def test_malformed_pipe_token(self):
        # '>|' is not the pipe operator.
        with self.assertRaises(SyntaxError):
            compile("x >| 1", "<test>", "exec")

    def test_nested_topic_ownership(self):
        # A topic in a nested pipeline body belongs only to the nested pipe,
        # so the outer body never references its own topic.
        self.assertSyntaxError("x |> (y |> f($))",
                               "pipeline body must reference '$'")

    def test_nested_pipe_inner_uses_outer_topic_is_ok(self):
        # The inner pipe's value may use the outer topic; the inner body must
        # use the inner topic.  This compiles.
        code = compile("v |> (w |> f($) if $ > 0 else 0)",
                       "<test>", "eval")
        self.assertIsNotNone(code)


# ---------------------------------------------------------------------------
# Compile / symtable / dis
# ---------------------------------------------------------------------------

def _hidden_topic_names(code):
    names = []
    for attr in ("co_varnames", "co_names", "co_cellvars", "co_freevars"):
        names.extend(getattr(code, attr, ()))
    return [n for n in names if isinstance(n, str) and n.startswith(".<pipe_topic")]


class CompileDisTests(PipelineTestCase):
    def test_hidden_local_in_function(self):
        def piped(x):
            return x |> (lambda: $ + 1)
        code = piped.__code__
        hidden = _hidden_topic_names(code)
        self.assertTrue(hidden, "expected a hidden topic binding in the function")
        # The hidden name is not a normal parameter.
        self.assertNotIn("$", code.co_varnames)

    def test_topic_is_not_user_addressable(self):
        # A user cannot name the hidden binding.
        code = compile("def f(x):\n"
                       "    return x |> (lambda: $ + 1)\n"
                       "f(1)\n"
                       "globals()['.<pipe_topic_0x0']\n", "<test>", "exec")
        with self.assertRaises(KeyError):
            exec(code, {})

    def test_no_ordinary_variable_leaks(self):
        # Piping does not create an ordinary Python-visible local named '$'.
        code = compile("def f(x):\n"
                       "    r = x |> ($ * 2)\n"
                       "    return r, [n for n in ()]\n"
                       "f(21)\n", "<test>", "exec")
        ns = {}
        exec(code, ns)
        self.assertNotIn("$", ns)

    def test_module_scope_artifact(self):
        # At module scope the hidden binding is a global and is therefore
        # visible as a (source-unspellable) key.  This is a documented
        # prototype artifact, not a supported feature.
        ns = {}
        exec(compile("y = 5 |> ($ + 1)\n", "<mod>", "exec"), ns)
        hidden = [k for k in ns if k.startswith(".<pipe_topic")]
        self.assertTrue(hidden, "expected a hidden module-level topic key")
        self.assertNotIn("$", ns)

    def test_disassembly_uses_hidden_name(self):
        def piped(x):
            return x |> (lambda: $ + 1)
        buf = io.StringIO()
        dis.dis(piped, file=buf)
        self.assertIn(".<pipe_topic", buf.getvalue())


# ---------------------------------------------------------------------------
# Semantics
# ---------------------------------------------------------------------------

class SemanticTests(PipelineTestCase):
    def test_basic(self):
        self.assertEqual(("hello" |> len($)), 5)
        self.assertEqual(("hello" |> len($) |> hex($)), "0x5")
        self.assertEqual((10 |> $ + 1 |> $ * 2), 22)
        self.assertEqual(([1, 2, 3] |> $[1]), 2)

    def test_position(self):
        self.assertEqual(("abc123" |> re.sub(r"\d+", "#", $)), "abc#")
        self.assertEqual([1, 2, 3] |> map(lambda x: x + 1, $) |> list($),
                         [2, 3, 4])

    def test_methods(self):
        self.assertEqual(("  hello " |> $.strip() |> $.upper()), "HELLO")

    def test_multiple_use_single_lhs_evaluation(self):
        calls = []

        def source():
            calls.append(1)
            return 5

        self.assertEqual((source() |> $ * $ + $), 30)
        self.assertEqual(calls, [1])

    def test_star(self):
        self.assertEqual(((2, 3) |> pow(*$)), 8)

    def test_starstar(self):
        self.assertEqual(({"base": 16} |> int("ff", **$)), 255)

    def test_evaluation_order(self):
        trace = []

        def lhs():
            trace.append("lhs")
            return 10

        def getf():
            trace.append("getf")
            return lambda a, b: a + b

        def arg():
            trace.append("arg")
            return 2

        self.assertEqual((lhs() |> getf()(arg(), $)), 12)
        self.assertEqual(trace, ["lhs", "getf", "arg"])

    def test_short_circuit(self):
        seen = []
        self.assertEqual((0 |> ($ and seen.append(1))), 0)
        self.assertEqual(seen, [])
        self.assertEqual((1 |> ($ and 7)), 7)

    def test_conditional_precedence_grouping(self):
        # (a + b) |> f($): arithmetic binds tighter than the pipe.
        self.assertEqual(((1 + 2) |> (lambda: $ + 100))(), 103)
        # x |> f($) + 1  =>  x |> (f($) + 1)
        self.assertEqual(10 |> ((lambda v: v + 100)($) + 1), 111)
        # x |> f($) if c else y  =>  (x |> f($)) if c else y
        self.assertEqual(((10 |> (lambda v: v + 100)($)) if True else 0), 110)
        # conditional inside a body needs grouping
        self.assertEqual(1 |> ((lambda v: v + 100)($) if True else 0), 101)

    def test_nested_topics_inner_value_uses_outer(self):
        r = [1, 2] |> (list($) |> (lambda: $[0]))
        self.assertEqual(r(), 1)

    def test_nested_topics_inner_body_shadows(self):
        r = 5 |> ($ |> (lambda: $ * 2))
        self.assertEqual(r(), 10)

    def test_lambda_closure_late_binding(self):
        v = [1, 2]
        grab = v |> (lambda: list(map(lambda t: t + 1, $)))
        self.assertEqual(grab(), [2, 3])

    def test_lambda_default_argument_snapshot(self):
        # The standard default-argument snapshot idiom works with the topic.
        value = 10
        fn = value |> (lambda s=$: s + 1)
        self.assertEqual(fn(), 11)

    def test_comprehension_iterable_element_filter(self):
        self.assertEqual([n for n in ("abc" |> list($))], ["a", "b", "c"])
        self.assertEqual(tuple(n for n in ("abc" |> list($))), ("a", "b", "c"))
        self.assertEqual(10 |> [$ for t in [1, 2]], [10, 10])
        self.assertEqual(10 |> tuple($ for t in [1, 2]), (10, 10))

    def test_comprehension_no_variable_leak(self):
        ns = {}
        exec(compile("def f(x):\n"
                     "    return [t for t in (x |> ($,))]\n"
                     "f(3)\n", "<test>", "exec"), ns)
        self.assertNotIn("$", ns)

    def test_async(self):
        async def af(v):
            return v * 3

        async def amain():
            return 14 |> await af($)

        self.assertEqual(asyncio.run(amain()), 42)

    def test_generator_yield_in_body(self):
        def gen(x):
            return x |> (yield $)
        self.assertEqual(list(gen(7)), [7])

    def test_walrus(self):
        def f(v):
            return v * 2

        def g(v):
            return v + 1

        saved = None
        result = 21 |> (saved := f($)) |> g($)
        self.assertEqual((result, saved), (43, 42))

    def test_exception_from_lhs_stops_body(self):
        def boom():
            raise ValueError

        with self.assertRaises(ValueError):
            boom() |> ($ * 2)

    def test_exception_from_stage_stops_later_stages(self):
        with self.assertRaises(ZeroDivisionError):
            1 |> ($ / 0) |> ($ + 1)


if __name__ == "__main__":
    unittest.main()