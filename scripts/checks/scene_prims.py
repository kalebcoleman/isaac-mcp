import omni.usd
stage = omni.usd.get_context().get_stage()
world = [str(p.GetPath()) for p in stage.GetPrimAtPath('/World').GetChildren()]
result = {'world_children': world,
          'ur5e_present': bool(stage.GetPrimAtPath('/World/UR5e')),
          'rg2_present': bool(stage.GetPrimAtPath('/World/RG2_Gripper')),
          'gravity_disabled_on_robot_links': sorted(
              str(p.GetPath()) for p in stage.Traverse()
              if str(p.GetPath()).startswith('/World/UR5e/') and p.HasAPI('PhysxRigidBodyAPI')
              and p.GetAttribute('physxRigidBody:disableGravity').Get() is not True)[:5]}
