::manbow.CompileFile("data/script/background/bg05_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

local t_eiki = this.SetMob(this.actor0, "mob_eiki");
t_eiki.mat_local.SetScaling(0.6, 0.6, 1.0);

local t_komachi = this.SetMob(this.actor0, "mob_komachi");
t_komachi.mat_local.SetScaling(0.6, 0.6, 1.0);

local t_miyafuru = this.SetMob(this.actor0, "mob_miyafuru");
t_miyafuru.mat_local.SetScaling(0.55, 0.55, 1.0);

local t_seiga = this.SetMob(this.actor0, "mob_seiga");
t_seiga.mat_local.SetScaling(0.55, 0.55, 1.0);

local t_yuyuko = this.SetMob(this.actor0, "mob_yuyuko");
t_yuyuko.mat_local.SetScaling(0.65, 0.65, 1.0);

local t_youmu = this.SetMob(this.actor0, "mob_youmu");
t_youmu.mat_local.SetScaling(0.65, 0.65, 1.0);

local t_ririka = this.SetMob(this.actor0, "mob_ririka");
t_ririka.mat_local.SetScaling(0.75, 0.75, 1.0);

local t_runasa = this.SetMob(this.actor0, "mob_runasa");
t_runasa.mat_local.SetScaling(0.75, 0.75, 1.0);

local t_meruran = this.SetMob(this.actor0, "mob_meruran");
t_meruran.mat_local.SetScaling(0.75, 0.75, 1.0);

local t_toranpet = this.SetMob(this.actor0, "mob_toranpet");
t_toranpet.mat_local.SetScaling(0.75, 0.75, 1.0);

local t_baiorin = this.SetMob(this.actor0, "mob_baiorin");
t_baiorin.mat_local.SetScaling(0.75, 0.75, 1.0);

local t_tojiko = this.SetMob(this.actor0, "mob_tojiko");
t_tojiko.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetUpdateFunctionType0("mob_meruran", 0.75, 0.071, 5.2);
this.SetUpdateFunctionType0("mob_ririka", 0.75, 0.06, 3.5);
this.SetUpdateFunctionType0("mob_runasa", 0.75, 0.066, 4.2);
this.SetUpdateFunctionType0("mob_baiorin", 0.75, 0.056, 4.2);
this.SetUpdateFunctionType0("mob_toranpet", 0.75, 0.066, 3.2);

foreach (a in this.actor_mob)
{
	a.actor.green = a.actor.green * 0.98;
	a.actor.red = a.actor.red * 0.77;
	a.actor.blue = a.actor.blue * 0.88;
}

t_tojiko.count <- 0;
t_tojiko.Update <- function()
{
	this.count++;
	this.mat_local.SetTranslation(sin(this.count / 1000.0) * 500.0, sin(this.count / 23.0) * 13.0, 0);
};

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
