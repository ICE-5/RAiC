from typing import Tuple
import torch
from torch.optim import Adam
from torch.autograd import Variable
from torch.nn import functional as F
from torch.utils.data.sampler import BatchSampler, SubsetRandomSampler
import numpy as np


from .net import LSTMPolicy
from .transition import Transition, Buffer


class PPO(object):
    def __init__(self, env, args, writer):
        self.num_epochs = args.num_epochs
        self.num_episodes = args.num_episodes
        self.max_global_step = args.max_global_step
        self.rollout_size = args.rollout_size
        self.num_agents = args.num_agents
        self.encode_dim = args.encode_dim
        self.gamma = args.gamma
        self.lam = args.lam
        self.lr = args.lr
        self.coeff_entropy = args.coeff_entropy
        self.clip_value = args.clip_value
        self.batch_size = args.batch_size

        # writer
        self.writer = writer

        # policy
        self.policy_type = args.policy_type
        if self.policy_type == "ppo-lstm":
            self.policy = LSTMPolicy(obs_lidar_frames=args.obs_lidar_dim,
                                     obs_other_dim=args.obs_other_dim,
                                     act_dim=args.act_dim,
                                     encode_dim=args.encode_dim)
            self.optim = Adam(self.policy.parameters(), lr=self.lr)

        # env
        self.env = env
        self.env_mode = args.env_mode
        if self.env_mode == "unity":
            self.behavior_name = args.behavior_name

    def train(self):
        buffer: Buffer = []
        global_update = 0
        global_step = 0

        for episode in range(self.num_episodes):
            self.env.reset()
            rollout_reward = 0.
            terminal = False
            if self.policy_type == "ppo-lstm":
                prev_a_hc = (torch.zeros(self.num_agents, self.encode_dim), torch.zeros(self.num_agents, self.encode_dim))
                prev_c_hc = (torch.zeros(self.num_agents, self.encode_dim), torch.zeros(self.num_agents, self.encode_dim))

            while not terminal:
                global_step += 1
                transition = self._step(prev_a_hc, prev_c_hc)
                buffer.append(transition)

                prev_a_hc = transition.a_hc
                prev_c_hc = transition.c_hc
                # re-init LSTM cell state and hidden state of collided agent
                # terminated_agents = torch.nonzero(transition.done).squeeze()
                # prev_a_hc = [x[i, :]= 0 for i in terminated_agents for x in a_hc]
                # prev_c_hc = [x[i, :]= 0 for i in terminated_agents for x in a_hc]
                # BEST: terminal means at least one agent is done (collided), need to calculate GAE
                terminal = torch.sum(transition.done) > 0

                if len(buffer) >= self.rollout_size:
                    global_update += 1
                    # TITLE: delete
                    

                    next_transition = self._step(prev_a_hc, prev_c_hc)
                    next_value = next_transition.value

                    # prepare 1: transform buffer
                    obs_arr, action_arr, reward_arr, done_arr, logprob_arr, value_arr, a_hc_arr, c_hc_arr = self._transform_buffer(buffer)
                    target_arr, adv_arr = self._get_advantage(reward_arr, value_arr, next_value, done_arr)
                    memory = (obs_arr, action_arr, logprob_arr, a_hc_arr, c_hc_arr, target_arr, adv_arr)
                    loss, _, _, _ = self._update(memory)
                    rollout_reward = torch.mean(reward_arr)
                    buffer = []
                    self.writer.add_scalar('Reward/Reward vs. update', rollout_reward, global_update)
                    self.writer.add_scalar('Reward/Reward vs. episode', rollout_reward, episode)
                    self.writer.add_scalar('Loss/Loss vs. update', loss, global_update)
                    self.writer.add_scalar('Loss/Loss vs. episode', loss, episode)
                    print(f"-----> Full buffer, update {global_update}\t reward {rollout_reward}\t loss {loss}")
            
            print(f"-----x terminated at episode {episode}")


    # NOTE: WIP
    def eval(self):
        pass

    # NOTE: DEBUGGED, need recheck
    def _step(self,
              prev_a_hc: Tuple[torch.tensor, torch.tensor] = None,
              prev_c_hc: Tuple[torch.tensor, torch.tensor] = None) -> Transition:     
        """Step the env with action.

        Args:
            prev_a_hc (Tuple[torch.tensor, torch.tensor], optional): actor state (hidden, cell) for LSTMCell. Defaults to None.
            prev_c_hc (Tuple[torch.tensor, torch.tensor], optional): critic state (hidden, cell) for LSTMCell. Defaults to None.

        Raises:
            ValueError: Wrong env mode

        Returns:
            Transition: Transition object with obs, action, reward, done, logprob, value, a_hc, c_hc
        """     
        if self.env_mode == "unity":
            # TODO: see MLAgents release 6 for use of terminal steps
            decision_steps, terminal_steps = self.env.get_steps(self.behavior_name)
            # obs
            obs_lidar: np.ndarray = decision_steps.obs[1][:, 2::3]                      # -> N x (obs_lidar_dim * obs_lidar_frames)
            obs_lidar: torch.tensor = torch.from_numpy(obs_lidar).float()
            obs_other: np.ndarray = decision_steps.obs[2]                               # -> N x obs_other_dim
            obs_other: torch.tensor = torch.from_numpy(obs_other).float()
            obs = (obs_lidar, obs_other)
            # reward
            reward: np.ndarray = decision_steps.reward                                  # -> N,
            reward: torch.tensor = torch.from_numpy(reward).float()
            # done, handle terminated (collided) agents
            terminated_agents = terminal_steps.agent_id
            done: list = [True if i in terminated_agents else False for i in range(self.num_agents)]
            done: torch.tensor = torch.tensor(done)

            # TODO: not sure if this is correct
            with torch.no_grad():
                value, action, logprob, _, a_hc, c_hc = self._get_clipped_action(obs, [-1., 1.], prev_a_hc, prev_c_hc)
                    
            transition = Transition(obs, action, reward, done, logprob, value, a_hc, c_hc)
            # TODO: is the done here necessary? Avoid irregular respawn behavior
            self.env.set_actions(self.behavior_name, action.numpy())
            self.env.step()
            return transition
        else:
            raise ValueError("Unsupported environment")

    # NOTE: STALE
    def _get_action(self,
                    obs: Tuple[np.ndarray, np.ndarray],
                    a_hc: Tuple[torch.tensor, torch.tensor] = None,
                    c_hc: Tuple[torch.tensor, torch.tensor] = None) -> Tuple[torch.tensor, ...]:
        return self.policy(obs, a_hc, c_hc)

    # NOTE: DEBUGGED
    def _get_clipped_action(self,
                            obs: Tuple[np.ndarray, np.ndarray],
                            action_bound: Tuple[int, int],
                            a_hc: Tuple[torch.tensor, torch.tensor] = None,
                            c_hc: Tuple[torch.tensor, torch.tensor] = None) -> Tuple[torch.tensor, ...]:
        """Get *clipped* action by step through policy network. 

        Args:
            obs (Tuple[np.ndarray, np.ndarray]): observation
            action_bound (Tuple[int, int]): clipping bound
            a_hc (Tuple[torch.tensor, torch.tensor], optional): actor state (hidden, cell) for LSTMCell. Defaults to None.
            c_hc (Tuple[torch.tensor, torch.tensor], optional): critic state (hidden, cell) for LSTMCell. Defaults to None.

        Returns:
            Tuple[torch.tensor, ...]: same as forward output
        """
        value, action, logprob, mean, a_hc, c_hc = self.policy(obs, a_hc, c_hc)
        clipped_action = torch.clamp(action, action_bound[0], action_bound[1])
        return value, clipped_action, logprob, mean, a_hc, c_hc

    # NOTE: DEBUGGED, need verify GAE
    def _get_advantage(self, reward_arr, value_arr, next_value, done_arr):
        T, N = reward_arr.shape
        value_arr = torch.cat((value_arr, next_value.unsqueeze(0)), dim=0)
        done_arr = done_arr.float()

        target_arr = torch.zeros(T, N)
        gae = torch.zeros(N)

        for t in range(T - 1, -1, -1):
            delta = reward_arr[t, :] + self.gamma * value_arr[t + 1, :] * (1 - done_arr[t, :]) - value_arr[t, :]
            gae = delta + self.gamma * self.lam * (1 - done_arr[t, :]) * gae

            target_arr[t, :] = gae + value_arr[t, :]

        adv_arr = target_arr - value_arr[:-1, :]
        return target_arr, adv_arr

    def _update(self, memory):
        obs, action, logprob, a_hc, c_hc, target, adv = memory
        N = target.shape[0] * target.shape[1]
        adv = (adv - adv.mean()) / adv.std()
        obs_lidar, obs_other = obs
        a_h, a_c = a_hc
        c_h, c_c = c_hc

        # reshape
        obs_lidar, obs_other, action, a_h, a_c, c_h, c_c = map(lambda x: x.view(N, -1), [obs_lidar, obs_other, action, a_h, a_c, c_h, c_c])
        logprob, target, adv = map(lambda x: x.view(-1, 1).squeeze(), [logprob, target, adv])

        info_p_loss, info_v_loss, info_entropy = 0., 0., 0.
        info_loss = 0.
        for _ in range(self.num_epochs):
            sampler = BatchSampler(SubsetRandomSampler(list(range(N))),
                                   batch_size=self.batch_size,
                                   drop_last=False)
            for idxs in sampler:
                b_obs_lidar, b_obs_other, b_action, b_logprob, b_a_h, b_a_c, b_c_h, b_c_c, b_target, b_adv = \
                    map(lambda x: x[idxs].requires_grad_(), [obs_lidar, obs_other, action, logprob, a_h, a_c, c_h, c_c, target, adv])
                b_obs = (b_obs_lidar, b_obs_other)
                b_a_hc = (b_a_h, b_a_c)
                b_c_hc = (b_c_h, b_c_c)
                # loss
                new_value, new_logprob, entropy = self.policy.evaluate_actions(b_obs, b_a_hc, b_c_hc, b_action)
                ratio = torch.exp(new_logprob - b_logprob)
                surrogate_1 = ratio * b_adv
                surrogate_2 = torch.clamp(ratio, 1-self.clip_value, 1+self.clip_value) * b_adv
                p_loss = - torch.min(surrogate_1, surrogate_2).mean()
                v_loss = F.mse_loss(new_value, b_target)
                loss = p_loss + 20 * v_loss - self.coeff_entropy * entropy

                self.optim.zero_grad()
                loss.backward()
                self.optim.step()

                info_p_loss += p_loss.detach()
                info_v_loss += v_loss.detach()
                info_entropy += entropy.detach()
                info_loss += loss.detach()

        return tuple(map(lambda x: x / (self.num_epochs * len(sampler)), [info_loss, info_p_loss, info_v_loss, info_entropy]))

    # NOTE: DEBUGGED, double-check if time allowed
    def _transform_buffer(self, buffer: Buffer) -> Tuple[torch.tensor]:
        """Map reduce collected transition buffer.

        Args:
            buffer (Buffer): A list of transitions

        Returns:
            Tuple[torch.tensor]: Grouped categories, obs, action, reward, done, logprob, value, a_hc, c_hc
        """        
        L1 = ['action', 'reward', 'done', 'logprob', 'value']
        L2 = ['obs', 'a_hc', 'c_hc']

        cpnt_1 = {key: getattr(buffer[0], key).unsqueeze(0) for key in L1}
        cpnt_2 = {key: (getattr(buffer[0], key)[0].unsqueeze(0),
                        getattr(buffer[0], key)[1].unsqueeze(0)) for key in L2}

        for idx in range(1, len(buffer)):
            cpnt_1 = {key: torch.cat((cpnt_1[key], getattr(buffer[idx], key).unsqueeze(0)), dim=0) for key in L1}
            cpnt_2 = {key: (torch.cat((cpnt_2[key][0], getattr(buffer[idx], key)[0].unsqueeze(0)), dim=0),
                            torch.cat((cpnt_2[key][1], getattr(buffer[idx], key)[1].unsqueeze(0)), dim=0)) for key in L2}
        
        # sanity check of output dim
        assert cpnt_1['action'].shape[0] == self.rollout_size
        assert cpnt_1['action'].shape[1] == self.num_agents

        return (cpnt_2['obs'], cpnt_1['action'], cpnt_1['reward'], cpnt_1['done'], cpnt_1['logprob'], cpnt_1['value'], cpnt_2['a_hc'], cpnt_2['c_hc'])
