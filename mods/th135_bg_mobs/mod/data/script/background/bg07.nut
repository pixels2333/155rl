::manbow.CompileFile("data/script/background/bg07_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

this.SetMob(this.actor0, "mob_kappaA");

local t_kappaB = this.SetMob(this.actor0, "mob_kappaB");
t_kappaB.mat_local.SetScaling(0.7, 0.7, 1.0);

local t_kappaC = this.SetMob(this.actor0, "mob_kappaC");
t_kappaC.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_kappaD = this.SetMob(this.actor0, "mob_kappaD");
t_kappaD.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_kappaE = this.SetMob(this.actor0, "mob_kappaE");
t_kappaE.mat_local.SetScaling(0.9, 0.9, 1.0);

this.SetMob(this.actor0, "mob_kanako");
this.SetMob(this.actor0, "mob_suwako");
this.SetMob(this.actor0, "mob_sanae");
this.SetMob(this.actor0, "mob_minoriko");
this.SetMob(this.actor0, "mob_sizuha");
this.SetMob(this.actor0, "mob_hina");

local t_hatate = this.SetMob(this.actor0, "mob_hatate");
t_hatate.mat_local.SetScaling(0.5, 0.5, 1.0);

local t_aya = this.SetMob(this.actor0, "mob_aya");
t_aya.mat_local.SetScaling(0.5, 0.5, 1.0);

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
