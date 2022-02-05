#!/usr/bin/env pytest
import collections
import functools
import math

import torch

import torchdynamo.testing
from torchdynamo import eval_frame
from torchdynamo.convert_frame import convert_frame
from torchdynamo.convert_frame import convert_frame_assert
from torchdynamo.testing import CompileCounter
from torchdynamo.testing import same
from torchdynamo.testing import unsupported

mytuple = collections.namedtuple("mytuple", ["a", "b", "ab"])


class MiscTests(torchdynamo.testing.TestCase):
    def test_boolarg(self):
        def boolarg(aa, bb, flag):
            if flag:
                return aa - bb
            else:
                return bb - aa

        a = torch.randn(10, 10)
        b = torch.randn(10, 10)
        correct1 = boolarg(a, b, True)
        correct2 = boolarg(a, b, False)
        correct3 = boolarg(a, b, None)
        counter = CompileCounter()
        with eval_frame.optimize(convert_frame_assert(counter)):
            val1 = boolarg(a, b, True)
            val2 = boolarg(a, b, False)
            val3 = boolarg(a, b, None)
            val4 = boolarg(a, b, True)
        self.assertTrue(same(val1, correct1))
        self.assertTrue(same(val2, correct2))
        self.assertTrue(same(val3, correct3))
        self.assertTrue(same(val4, correct1))
        self.assertEqual(counter.frame_count, 3)

    def test_callpacked(self):
        def call_packed(args):
            a, b, c = args
            return a - b * c

        counter = CompileCounter()
        a = torch.randn(10, 10)
        b = torch.randn(10, 10)
        c = torch.randn(10, 10)
        correct = call_packed([a, b, c])
        with eval_frame.optimize(convert_frame_assert(counter)):
            val1 = call_packed([a, b, c])
            val2 = call_packed((a, b, c))
            val3 = call_packed([a, b, c])
            val4 = call_packed((a, b, c))
        self.assertTrue(same(val1, correct))
        self.assertTrue(same(val2, correct))
        self.assertTrue(same(val3, correct))
        self.assertTrue(same(val4, correct))
        self.assertEqual(counter.frame_count, 2)

    def test_raises(self):
        def fn(a, b, c, cls):
            x = a + b - c * 10
            raise cls(str(x))

        counter = CompileCounter()
        a = torch.randn(10, 10)
        b = torch.randn(10, 10)
        c = torch.randn(10, 10)
        with eval_frame.optimize(convert_frame(counter)):
            self.assertRaises(AssertionError, lambda: fn(a, b, c, AssertionError))
        self.assertEqual(counter.frame_count, 1)
        self.assertEqual(counter.op_count, 3)

    def test_inplace(self):
        def inplace1(a, b):
            o = torch.empty((10, 10))
            o.copy_(a)
            o -= b
            return o

        torchdynamo.testing.standard_test(self, inplace1, 2, expected_ops=3)

    def test_unpack4(self):
        def unpack4(a, b):
            a = a[:5, :]
            b = b[:5, :]
            x, y = a.size()
            o = torch.empty((x, y))
            o.copy_(a / b)
            return o

        torchdynamo.testing.standard_test(self, unpack4, 2, expected_ops=5)

    def test_unpack5(self):
        def unpack5(a, b):
            a = a[:5, :]
            b = b[:5, :]
            x, y = a.shape
            o = torch.empty((x, y))
            o.copy_(a / b)
            return o

        torchdynamo.testing.standard_test(self, unpack5, 2, expected_ops=5)

    def test_matmul1(self):
        def matmul_op1(a, b):
            return a @ b

        # TODO(jansel): FX doesn't support this, should add upstream support
        torchdynamo.testing.standard_test(self, matmul_op1, 2, expected_ops=1)

    def test_builtin_isinstance(self):
        def fn(x):
            t = torch.arange(1, 3)
            a = isinstance(x, torch.Tensor)
            b = isinstance(t, torch.Tensor)
            c = isinstance(x, int)
            d = isinstance(3, int)
            e = isinstance([1, 2, 3], list)
            f = isinstance({"foo": 1, "bar": 2}, dict)
            res = [a, b, c, d, e, f]
            # Can't run yet due to other unimplemented instructions
            # res += [isinstance(torch.nn.LazyLinear(2, 3), torch.nn.Linear)]
            return res

        torchdynamo.testing.standard_test(self, fn, 1, expected_ops=1)

    def test_fold(self):
        def fn(a):
            return a + math.sqrt(63)

        torchdynamo.testing.standard_test(self, fn, 1, expected_ops=1)

    def test_shape_unpack(self):
        def fn(x):
            a, b = x.size()
            return x * b

        i = torch.randn(5, 10)
        r1 = fn(i)
        with eval_frame.optimize(convert_frame_assert(lambda gm: gm.forward)):
            r2 = fn(i)
        self.assertTrue(same(r1, r2))

    def test_empty_list(self):
        def fn(x, ll):
            if len(ll) == 0 and not ll and ll is not None:
                return x + 1

        i = torch.randn(5, 10)
        r1 = fn(i, [])
        with eval_frame.optimize(convert_frame_assert(lambda gm: gm.forward)):
            r2 = fn(i, [])
            r3 = fn(i, tuple())
        self.assertTrue(same(r1, r2))
        self.assertTrue(same(r1, r3))

    def test_config_obj(self):
        class Cfg:
            def __init__(self):
                self.val = 0.5
                self.count = 3

        def fn(x, cfg):
            for i in range(cfg.count):
                x = x + cfg.val
            return x

        cfg1 = Cfg()
        cfg1.val = 1.0
        cfg2 = Cfg()
        v = torch.zeros(1)
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            v = fn(v, cfg1)  # 3
            v = fn(v, cfg2)  # 4.5
            cfg2.count = 1
            v = fn(v, cfg2)  # 5
            cfg2.val = 2.0
            v = fn(v, cfg2)  # 7
        self.assertEqual(v[0], 7)
        self.assertEqual(cnts.op_count, 8)

    def test_size_input(self):
        def fn(x, s):
            a, b = s
            return x + (a - b)

        v = torch.zeros(10, 20)
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn(v, v.size())[0, 0], -10)
            self.assertEqual(fn(v, (10, 20))[0, 0], -10)
            self.assertEqual(fn(v, [10, 20])[0, 0], -10)
        self.assertEqual(cnts.op_count, 2)

    def test_cell_output1(self):
        out = None

        def fn(a, b):
            nonlocal out
            out = a + b * 10

        v = torch.Tensor([100])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertIsNone(fn(v, v))
            self.assertEqual(out[0], 1100)
        self.assertEqual(cnts.op_count, 2)

    def test_cell_output2(self):
        out = None

        def fn(a, b):
            nonlocal out
            c = unsupported(a, b)
            out = a + b * 10 + c

        v = torch.Tensor([100])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame(cnts)):
            self.assertIsNone(fn(v, v))
            self.assertEqual(out[0], 1200)
        self.assertEqual(cnts.op_count, 3)

    def test_return_nested_function(self):
        out = None

        def fn(a, b):
            nonlocal out
            c = a + b
            d = a + 1.0

            def fn2(f: int = 7, g: float = 9.0):
                nonlocal out
                out = a + b * 10
                return c * f - d * g

            return fn2

        v1 = torch.Tensor([100])
        v2 = torch.Tensor([200])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn(v1, v2)(1.5)[0], -459)
            self.assertEqual(out[0], 2100)
        self.assertEqual(cnts.frame_count, 2)
        self.assertEqual(cnts.op_count, 7)

    def test_tensor_dict1(self):
        def fn(inputs):
            return inputs["a"] - inputs["b"] * 1.5

        v1 = torch.Tensor([100])
        v2 = torch.Tensor([200])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn({"a": v1, "b": v2})[0], -200)
        self.assertEqual(cnts.frame_count, 1)
        self.assertEqual(cnts.op_count, 2)

    def test_tensor_dict2(self):
        def fn1(inputs):
            total = torch.zeros(1)
            for k, v in inputs.items():
                total += v
            return total

        def fn2(inputs):
            total = torch.zeros(1)
            for v in inputs.values():
                total += v
            return total

        def fn3(inputs):
            total = torch.zeros(1)
            for k in inputs.keys():
                total += inputs[k]
            return total

        v1 = torch.Tensor([100])
        v2 = torch.Tensor([200])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn1({"a": v1, "b": v2})[0], 300)
            self.assertEqual(fn2({"a": v1, "b": v2})[0], 300)
            self.assertEqual(fn3({"a": v1, "b": v2})[0], 300)
        self.assertEqual(cnts.frame_count, 3)
        self.assertEqual(cnts.op_count, 9)

    def test_dictcomp(self):
        def fn1(inputs):
            return {k: v + 1 for k, v in inputs.items()}

        v1 = torch.Tensor([100])
        v2 = torch.Tensor([200])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn1({"a": v1, "b": v2})["a"], 101)
            self.assertEqual(fn1({"a": v1, "b": v2})["b"], 201)
        self.assertEqual(cnts.frame_count, 1)
        self.assertEqual(cnts.op_count, 2)

    def test_listcomp(self):
        def fn2(inputs):
            return torch.sum(torch.cat([v + 1 for k, v in inputs.items()], 0))

        v1 = torch.Tensor([100])
        v2 = torch.Tensor([200])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn2({"a": v1, "b": v2}), 302)
        self.assertEqual(cnts.frame_count, 1)
        self.assertEqual(cnts.op_count, 4)

    def test_is_floating_point(self):
        def fn(a, b):
            x = a + 1.0
            if torch.is_floating_point(b):
                x = x + b
            return x + 2.0

        return torchdynamo.testing.standard_test(self, fn=fn, nargs=2, expected_ops=3)

    def test_is_floating_point2(self):
        def fn(a, b):
            x = a + 1.0
            if b.is_floating_point():
                x = x + b
            return x + 2.0

        return torchdynamo.testing.standard_test(self, fn=fn, nargs=2, expected_ops=3)

    def test_is_tensor(self):
        def fn(a, b):
            x = a + 1.0
            if torch.is_tensor(b):
                x = x + b
            return x + 2.0

        return torchdynamo.testing.standard_test(self, fn=fn, nargs=2, expected_ops=3)

    def test_numel(self):
        def fn(a):
            return a + a.numel() + torch.numel(a)

        return torchdynamo.testing.standard_test(self, fn=fn, nargs=1, expected_ops=2)

    def test_pair(self):
        def fn(a):
            return (
                torch.zeros(torch.nn.modules.utils._pair(a.size()))
                + a
                + torch.ones(torch.nn.modules.utils._ntuple(3)(3)).sum()
            )

        return torchdynamo.testing.standard_test(self, fn=fn, nargs=1, expected_ops=5)

    def test_tensor_item(self):
        def fn(a, b):
            return (a + b).sum().item()

        v1 = torch.randn((10, 10))
        v2 = torch.randn((10, 10))
        correct = fn(v1, v2)
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame(cnts)):
            self.assertEqual(fn(v1, v2), correct)
        self.assertEqual(cnts.frame_count, 1)
        self.assertEqual(cnts.op_count, 2)

    def test_namedtuple1(self):
        def fn(a, b):
            tmp = mytuple(a, b, a + b)
            return mytuple(tmp.a, tmp[1], tmp.ab + b)

        v1 = torch.Tensor([10])
        v2 = torch.Tensor([20])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn(v1, v2).ab, 50)
        self.assertEqual(cnts.frame_count, 1)
        self.assertEqual(cnts.op_count, 2)

    def test_namedtuple2(self):
        def fn(packed):
            a, b, c = packed
            if hasattr(packed, "b"):
                b = packed.b + 1
            c = packed[2]
            return a + b + c

        v1 = torch.Tensor([1])
        v2 = torch.Tensor([2])
        v3 = torch.Tensor([3])
        cnts = torchdynamo.testing.CompileCounter()
        with eval_frame.optimize(convert_frame_assert(cnts)):
            self.assertEqual(fn(mytuple(v1, v2, v3))[0], 7)
        self.assertEqual(cnts.frame_count, 1)
        self.assertEqual(cnts.op_count, 3)

    def test_range_input(self):
        def fn(a, rng):
            x = a
            for i in rng:
                x = x + i
            return x

        return torchdynamo.testing.standard_test(
            self, fn=functools.partial(fn, rng=range(3)), nargs=1, expected_ops=3
        )

    def test_no_grad(self):
        def fn1(a, b):
            x = a + 1
            # redundant no_grad should get ignored
            with torch.no_grad():
                x = x + b
            x = x + 2
            return x

        def fn2(a, b):
            x = a + 1
            with torch.set_grad_enabled(False):
                x = x + b
            x = x + 2
            return x

        def fn3(a, b):
            x = a + 1
            with torch.enable_grad():
                x = x + b
            x = x + 2
            return x

        def fn4(a, b):
            x = a + 1
            with torch.set_grad_enabled(True):
                if torch.is_grad_enabled():
                    x = x + b
            x = x + 2
            return x

        with torch.no_grad():
            torchdynamo.testing.standard_test(self, fn=fn1, nargs=2, expected_ops=3)
            torchdynamo.testing.standard_test(self, fn=fn2, nargs=2, expected_ops=3)
            torchdynamo.testing.standard_test(self, fn=fn3, nargs=2, expected_ops=5)
            torchdynamo.testing.standard_test(self, fn=fn4, nargs=2, expected_ops=5)
        with torch.enable_grad():
            torchdynamo.testing.standard_test(self, fn=fn1, nargs=2, expected_ops=5)
            torchdynamo.testing.standard_test(self, fn=fn2, nargs=2, expected_ops=5)
            torchdynamo.testing.standard_test(self, fn=fn3, nargs=2, expected_ops=3)
            torchdynamo.testing.standard_test(self, fn=fn4, nargs=2, expected_ops=3)

    def test_build_tuple_unpack(self):
        def fn1(a, b, c):
            return a - b / c

        def fn2(a, b, c):
            tmp1 = (a,)
            tmp2 = (b, c)
            args = (*tmp1, *tmp2)
            return fn1(*args)

        def fn3(a, *args):
            return fn1(a, *args)

        torchdynamo.testing.standard_test(self, fn=fn2, nargs=3, expected_ops=2)
        torchdynamo.testing.standard_test(self, fn=fn3, nargs=3, expected_ops=2)