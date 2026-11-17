::manbow.CompileFile("data/script/background/bg06_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

local t_misutia = this.SetMob(this.actor0, "mob_misutia");
t_misutia.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetMob(this.actor0, "mob_lily", "mob_haneA");
this.SetMob(this.actor0, "mob_lily");
this.SetMob(this.actor0, "mob_star", "mob_star_hane");
this.SetMob(this.actor0, "mob_star");
this.SetMob(this.actor0, "mob_sany", "mob_sany_hane");
this.SetMob(this.actor0, "mob_sany");
this.SetMob(this.actor0, "mob_luna", "mob_luna_hane");
this.SetMob(this.actor0, "mob_luna");

local t_medi = this.SetMob(this.actor0, "mob_medi");
t_medi.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_riguru = this.SetMob(this.actor0, "mob_riguru");
t_riguru.mat_local.SetScaling(-0.9, 0.9, 1.0);

local t_rumia = this.SetMob(this.actor0, "mob_rumia");
t_rumia.mat_local.SetScaling(0.9, 0.9, 1.0);

local t_chiruno = this.SetMob(this.actor0, "mob_chiruno");
t_chiruno.mat_local.SetScaling(-1.0, 1.0, 1.0);

this.SetMob(this.actor0, "mob_lety");
this.SetMob(this.actor0, "mob_yuuka");

this.SetUpdateFunctionType0("mob_chiruno", 0.8, 0.063, 4.0);
this.SetUpdateFunctionType0("mob_rumia", 0.8, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_riguru", 0.8, 0.065, 5.5);
this.SetUpdateFunctionType0("mob_lily", 0.9, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_sany", 0.9, 0.075, 4.0);
this.SetUpdateFunctionType0("mob_star", 0.9, 0.07, 5.2);
this.SetUpdateFunctionType0("mob_luna", 0.9, 0.065, 5.1);

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
