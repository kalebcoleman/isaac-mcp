"""USD pose reads/writes for the twin's referenced object bodies.

Work on the composed transform, not the existence of an inactive orient attribute.
Only simple, static TRS stacks are supported; preserve source layers and scale.
"""

import math

from pxr import Gf, Usd, UsdGeom


def _vector(value, length, label):
    values = tuple(float(v) for v in value)
    if len(values) != length or not all(math.isfinite(v) for v in values):
        raise ValueError(f"{label} must contain {length} finite numbers")
    return values


def _xform(prim):
    if not prim or not prim.IsValid() or prim.IsInstanceProxy():
        raise ValueError("Object body must be a valid, editable prim")
    xf = UsdGeom.Xformable(prim)
    if not xf:
        raise ValueError("Object body is not transformable")
    ops = xf.GetOrderedXformOps()
    order = []
    for op in ops:
        name = op.GetOpName()
        if op.IsInverseOp() or op.GetNumTimeSamples():
            raise ValueError("Animated or inverse object transform ops are not supported")
        if name == 'xformOp:translate':
            order.append(0)
        elif name in ('xformOp:orient', 'xformOp:rotateX', 'xformOp:rotateY',
                      'xformOp:rotateZ', 'xformOp:rotateXYZ', 'xformOp:rotateXZY',
                      'xformOp:rotateYXZ', 'xformOp:rotateYZX', 'xformOp:rotateZXY',
                      'xformOp:rotateZYX'):
            order.append(1)
        elif name == 'xformOp:scale':
            order.append(2)
        else:
            raise ValueError(f"Unsupported object transform op: {name}")
    if order != sorted(set(order)):
        raise ValueError("Object pose requires a translate/rotation/scale transform stack")
    return xf


def read_local_pose(prim):
    xf = _xform(prim)
    transform = Gf.Transform(xf.GetLocalTransformation(Usd.TimeCode.Default()))
    quat = transform.GetRotation().GetQuat()
    return {
        'position': dict(zip('xyz', map(float, transform.GetTranslation()))),
        'quaternion': dict(zip('wxyz', (float(quat.GetReal()), *map(float, quat.GetImaginary())))),
        'scale': dict(zip('xyz', map(float, transform.GetScale()))),
    }


def set_local_pose(prim, position, quaternion_wxyz, scale=None):
    """Set a local TRS pose, activating orient and retaining the existing scale.

    Referenced Euler rotation ops stay in their source layer but are replaced in the
    composed op order. Existing float/double attribute precision is respected.
    """
    xf = _xform(prim)
    position = _vector(position, 3, 'position')
    q = _vector(quaternion_wxyz, 4, 'quaternion [w,x,y,z]')
    norm = math.sqrt(sum(v * v for v in q))
    if norm < 1e-12 or not math.isfinite(norm):
        raise ValueError('quaternion must have nonzero finite length')
    q = tuple(v / norm for v in q)
    if scale is None:
        attr = prim.GetAttribute('xformOp:scale')
        scale = attr.Get() if attr and attr.Get() is not None else (1, 1, 1)
    scale = _vector(scale, 3, 'scale')
    if any(v <= 0 for v in scale):
        raise ValueError('Object pose requires positive scale')
    # Validate even inactive attributes before changing the stage.
    existing = {}
    for name, typ in [('translate', UsdGeom.XformOp.TypeTranslate),
                      ('orient', UsdGeom.XformOp.TypeOrient),
                      ('scale', UsdGeom.XformOp.TypeScale)]:
        attr = prim.GetAttribute('xformOp:' + name)
        if attr:
            op = UsdGeom.XformOp(attr)
            if op.GetOpType() != typ or op.GetNumTimeSamples():
                raise ValueError(f'Unsupported object {name} attribute')
            existing[name] = op
    reset = xf.GetResetXformStack()
    translate = existing.get('translate') or xf.AddTranslateOp()
    orient = existing.get('orient') or xf.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
    scaling = existing.get('scale') or xf.AddScaleOp(UsdGeom.XformOp.PrecisionDouble)
    def vec(op, values):
        cls = {UsdGeom.XformOp.PrecisionDouble: Gf.Vec3d,
               UsdGeom.XformOp.PrecisionFloat: Gf.Vec3f,
               UsdGeom.XformOp.PrecisionHalf: Gf.Vec3h}[op.GetPrecision()]
        return cls(*values)
    qcls = {UsdGeom.XformOp.PrecisionDouble: Gf.Quatd,
            UsdGeom.XformOp.PrecisionFloat: Gf.Quatf,
            UsdGeom.XformOp.PrecisionHalf: Gf.Quath}[orient.GetPrecision()]
    if not (translate.Set(vec(translate, position)) and orient.Set(qcls(*q))
            and scaling.Set(vec(scaling, scale))
            and xf.SetXformOpOrder([translate, orient, scaling], reset)):
        raise RuntimeError('USD pose write failed; the stage may contain partial edits')
