::manbow.CompileFile("data/script/background/bg04_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

local t_syou = this.SetMob(this.actor0, "mob_syou");
t_syou.mat_local.SetScaling(0.75, 0.75, 1.0);
this.mob["mob_syou"].actor.red = 0.7;
this.mob["mob_syou"].actor.green = 0.64;
this.mob["mob_syou"].actor.blue = 0.88;

local t_kogasa = this.SetMob(this.actor0, "mob_kogasa");
t_kogasa.mat_local.SetScaling(0.9, 0.9, 1.0);

this.SetMob(this.actor0, "mob_kyouko");

local t_mistya = this.SetMob(this.actor0, "mob_mistya", "mob_misutia");
t_mistya.mat_local.SetScaling(0.7, 0.7, 1.0);

local t_hatate = this.SetMob(this.actor0, "mob_hatate");
t_hatate.mat_local.SetScaling(0.65, 0.65, 1.0);

local t_aya = this.SetMob(this.actor0, "mob_aya");
t_aya.mat_local.SetScaling(0.65, 0.65, 1.0);

this.SetMob(this.actor0, "mob_B");
this.SetMob(this.actor0, "mob_C");
this.SetMob(this.actor0, "mob_K");

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
