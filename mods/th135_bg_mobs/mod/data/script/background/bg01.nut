::manbow.CompileFile("data/script/background/bg01_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

this.SetMob(this.actor0, "mob_star", "mob_star_hane");
this.SetMob(this.actor0, "mob_star");
this.SetMob(this.actor0, "mob_yukari");
this.SetMob(this.actor0, "mob_luna", "mob_luna_hane");
this.SetMob(this.actor0, "mob_luna");
this.SetMob(this.actor0, "mob_sany", "mob_sany_hane");
this.SetMob(this.actor0, "mob_sany");
this.SetMob(this.actor0, "mob_youseiA", "mob_youseiA_hane");
this.SetMob(this.actor0, "mob_youseiA");
this.SetMob(this.actor0, "mob_youseiB", "mob_youseiB_hane");
this.SetMob(this.actor0, "mob_youseiB");
this.SetMob(this.actor0, "mob_sakuya");
this.SetMob(this.actor0, "mob_remilia");
this.SetMob(this.actor0, "mob_pachouly");
this.SetMob(this.actor0, "mob_chen");
this.SetMob(this.actor0, "mob_ran");

// TH155 bg01.msh placed shrine/trees under Root (scale 0.9) while leaving 00mob_* under root (scale 1.0).
// Scale 00mob_* world matrices by 0.9 to match TH13.5 bg01.msh exact 0.9x node coordinates.
foreach (a in this.mob)
{
	a.matrix.Scale(0.9, 0.9, 0.9);
}

this.SetUpdateFunctionType0("mob_sany", 1.0, 0.075, 4.0);
this.SetUpdateFunctionType0("mob_star", 1.0, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_luna", 1.0, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_youseiA", 1.0, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_youseiB", 1.0, 0.075, 4.0);

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
