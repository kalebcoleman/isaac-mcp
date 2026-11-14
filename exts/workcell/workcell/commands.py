"""Bridge handlers for the pack-backed workcell."""


class Commands:
    async def _cmd_workcell_quick_start(self, config_path=None, mode="physics", discard_changes=False):
        return await self.workcell.quick_start(config_path=config_path, mode=mode,
                                               discard_changes=discard_changes)

    def _cmd_workcell_status(self):
        return self.workcell.status()

    def _cmd_workcell_shutdown(self):
        return self.workcell.shutdown()

    def _cmd_robot_status(self):
        report = self.workcell.status()
        return {"state": report.get("state"), "robot": report.get("robot")}

    def _cmd_perception_status(self):
        report = self.workcell.status()
        return {"state": report.get("state"), "cameras": report.get("cameras")}

    def _cmd_jenga_status(self):
        report = self.workcell.status()
        return {"state": report.get("state"), "jenga": report.get("jenga")}

    def _cmd_jenga_build(self, profile=None, seed=None):
        return self.workcell.build_jenga(profile=profile, seed=seed)
