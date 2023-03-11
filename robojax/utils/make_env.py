from dataclasses import dataclass
from typing import Optional

import gymnasium
import gymnasium.vector
import jax
import numpy as np
from chex import Array
from gymnasium import spaces
from gymnasium.vector import AsyncVectorEnv, VectorEnv
from gymnasium.wrappers import RecordVideo, TimeLimit

import robojax.wrappers.maniskill2 as ms2wrappers


@dataclass
class EnvMeta:
    sample_obs: Array
    sample_acts: Array
    obs_space: spaces.Space  # Technically not always the right typing
    act_space: spaces.Space


def make_env(
    env_id: str,
    jax_env: bool,
    max_episode_steps: int,
    num_envs: Optional[int] = 1,
    seed: Optional[int] = 0,
    record_video_path: str = None,
    env_kwargs=dict(),
):
    """
    Utility function to create a jax/non-jax based environment given an env_id
    """
    is_brax_env = False
    is_gymnax_env = False
    if jax_env:
        import gymnax
        from brax import envs

        from robojax.wrappers.brax import BraxGymWrapper

        if env_id in gymnax.registered_envs:
            is_gymnax_env = True
        elif env_id in envs._envs:
            is_brax_env = True
        else:
            raise ValueError(f"Could not find environment {env_id} in gymnax or brax")
        if is_gymnax_env:
            env, env_params = gymnax.make(env_id)
        elif is_brax_env:
            env = envs.create(env_id, auto_reset=True)
            # TODO make brax gym gymnasium compatible
            env = BraxGymWrapper(env)
        # TODO add time limit wrapper of sorts
        sample_obs = env.reset(jax.random.PRNGKey(0))[0]
        sample_acts = env.action_space().sample(jax.random.PRNGKey(0))
        obs_space = env.observation_space()
        act_space = env.action_space()
    else:
        wrappers = []

        mani_skill2_env = False
        try:
            import mani_skill2.envs
            from mani_skill2.utils.registration import REGISTERED_ENVS
            from mani_skill2.utils.wrappers import RecordEpisode

            import robojax.experimental.envs.peginsertion
            import robojax.experimental.envs.pick_cube

            gymnasium.register(
                "LiftCube-v0", "mani_skill2.envs.pick_and_place.pick_cube:LiftCubeEnv"
            )
            # gymnasium.register("PickCube-v1", "mani_skill2.envs.pick_and_place.pick_cube:PickCubeEnv")
            gymnasium.register(
                "PickCube-v1", "robojax.experimental.envs.pick_cube:PickCubeEnv"
            )
            gymnasium.register(
                "PegInsertionSide-v1",
                "robojax.experimental.envs.peginsertion:PegInsertionSideEnv",
            )
            if env_id in REGISTERED_ENVS:
                mani_skill2_env = True
                wrappers.append(lambda x: ms2wrappers.ManiSkill2Wrapper(x))
                wrappers.append(lambda x: ms2wrappers.ContinuousTaskWrapper(x))
                stats_wrapper = ms2wrappers.PickCubeStats
                if "PickCube" in env_id:
                    stats_wrapper = ms2wrappers.PickCubeStats
                elif "PegInsertionSide" in env_id:
                    stats_wrapper = ms2wrappers.PegInsertionSideStats
                wrappers.append(lambda x: stats_wrapper(x))

        except:
            print("Skipping ManiSkill2 import")
            pass
        wrappers.append(lambda x: TimeLimit(x, max_episode_steps=max_episode_steps))
        if mani_skill2_env:

            def make_env(env_id, idx, record_video):
                def _init():
                    env = gymnasium.make(env_id, disable_env_checker=True, **env_kwargs)
                    if record_video and idx == 0:
                        env = RecordEpisode(env, record_video_path, info_on_video=True)
                    for wrapper in wrappers:
                        env = wrapper(env)
                    return env

                return _init

        else:

            def make_env(env_id, idx, record_video):
                def _init():
                    env = gymnasium.make(env_id, disable_env_checker=True, **env_kwargs)
                    if record_video and idx == 0:
                        env = RecordVideo(env, record_video_path)
                    for wrapper in wrappers:
                        env = wrapper(env)
                    return env

                return _init

        # create a vector env parallelized across CPUs with the given timelimit and auto-reset
        # env: VectorEnv = gymnasium.vector.make(env_id, num_envs=num_envs, wrappers=wrappers, disable_env_checker=True)
        env: VectorEnv = AsyncVectorEnv(
            [
                make_env(env_id, idx, record_video=record_video_path is not None)
                for idx in range(num_envs)
            ]
        )
        obs_space = env.single_observation_space
        act_space = env.single_action_space
        env.reset(seed=seed)
        sample_obs = obs_space.sample()
        sample_acts = act_space.sample()

    return env, EnvMeta(
        obs_space=obs_space,
        act_space=act_space,
        sample_obs=sample_obs,
        sample_acts=sample_acts,
    )
