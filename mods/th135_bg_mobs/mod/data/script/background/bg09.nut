::manbow.CompileFile("data/script/background/bg09_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

local t_paru = this.SetMob(this.actor0, "mob_paru");
t_paru.mat_local.SetScaling(0.7, 0.7, 1.0);

this.SetMob(this.actor0, "mob_lily", "mob_haneA");
local t_lily = this.mob["mob_lily"];
t_lily.mat_local.SetScaling(0.8, 0.8, 1.0);
this.SetMob(this.actor0, "mob_lily");

local t_sizuha = this.SetMob(this.actor0, "mob_sizuha");
t_sizuha.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_minoriko = this.SetMob(this.actor0, "mob_minoriko");
t_minoriko.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_hina = this.SetMob(this.actor0, "mob_hina");
t_hina.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_suwako = this.SetMob(this.actor0, "mob_suwako");
t_suwako.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_sanae = this.SetMob(this.actor0, "mob_sanae");
t_sanae.mat_local.SetScaling(0.8, 0.8, 1.0);

local t_syou = this.SetMob(this.actor0, "mob_syou");
t_syou.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetMob(this.actor0, "mob_tenko");
this.SetMob(this.actor0, "mob_iku");
this.SetMob(this.actor0, "mob_kogasa");
this.SetMob(this.actor0, "mob_kyouko");
this.SetMob(this.actor0, "mob_okuu");
this.SetMob(this.actor0, "mob_orin");
this.SetMob(this.actor0, "mob_satori");

local t_riguru = this.SetMob(this.actor0, "mob_riguru");
t_riguru.mat_local.SetScaling(0.9, 0.9, 1.0);

local t_chiruno = this.SetMob(this.actor0, "mob_chiruno");
t_chiruno.mat_local.SetScaling(0.9, 0.9, 1.0);

this.SetMob(this.actor0, "mob_luna", "mob_luna_hane");
local t_luna = this.mob["mob_luna"];
t_luna.mat_local.SetScaling(0.9, 0.9, 1.0);

this.SetMob(this.actor0, "mob_star", "mob_star_hane");
local t_star = this.mob["mob_star"];
t_star.mat_local.SetScaling(0.9, 0.9, 1.0);

this.SetMob(this.actor0, "mob_sany", "mob_sany_hane");
local t_sany = this.mob["mob_sany"];
t_sany.mat_local.SetScaling(0.9, 0.9, 1.0);
this.SetMob(this.actor0, "mob_star");
this.SetMob(this.actor0, "mob_luna");
this.SetMob(this.actor0, "mob_sany");

foreach (a in this.actor_mob)
{
	a.actor.green = a.actor.green * 0.8;
	a.actor.blue = a.actor.blue * 0.8;
}

this.SetUpdateFunctionType0("mob_chiruno", 0.9, 0.063, 4.0);
this.SetUpdateFunctionType0("mob_riguru", 0.9, 0.065, 5.5);
this.SetUpdateFunctionType0("mob_lily", 0.9, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_sany", 0.9, 0.075, 4.0);
this.SetUpdateFunctionType0("mob_star", 0.9, 0.07, 5.2);
this.SetUpdateFunctionType0("mob_luna", 0.9, 0.065, 5.1);
this.SetUpdateFunctionType0("mob_tenko", 0.9, 0.055, 7.1);
this.SetUpdateFunctionType0("mob_iku", 0.9, 0.035, 4.1);

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
