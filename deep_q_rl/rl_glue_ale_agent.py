#!/usr/bin/env python
"""
This uses the skeleton_agent.py file from the Python-codec of rl-glue
as a starting point.


Author: Nathan Sprague
"""

#
# Copyright (C) 2008, Brian Tanner
#
#http://rl-glue-ext.googlecode.com/
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import copy
import os
import cPickle
from rlglue.agent.Agent import Agent
from rlglue.agent import AgentLoader as AgentLoader
from rlglue.types import Action
from rlglue.types import Observation
from rlglue.utils import TaskSpecVRLGLUE3
import time

import random
import numpy as np
import cv2

import argparse

import matplotlib.pyplot as plt

import cnn_q_learner
import ale_data_set
import theano

import sys
sys.setrecursionlimit(10000)

floatX = theano.config.floatX

IMAGE_WIDTH = 160
IMAGE_HEIGHT = 210

CROPPED_WIDTH = 84
CROPPED_HEIGHT = 84

# Number of rows to crop off the bottom of the (downsampled) screen.
# This is appropriate for breakout, but it may need to be modified
# for other games. 
CROP_OFFSET = 8


class NeuralAgent(Agent):
    randGenerator=random.Random()

    def __init__(self):
        """
        Mostly just read command line arguments here. We do this here
        instead of agent_init to make it possible to use --help from
        the command line without starting an experiment.
        """

        # Handle command line argument:
        parser = argparse.ArgumentParser(description='Neural rl agent.')
        parser.add_argument('--learning_rate', type=float, default=.0002,
                            help='Learning rate')
        parser.add_argument('--rms_decay', type=float, default=.99,
                            help='Decay rate for rms_prop')
        parser.add_argument('--momentum', type=float, default=0,
                            help='Momentum term for Nesterov momentum.')
        parser.add_argument('--discount', type=float, default=.95,
                            help='Discount rate')
        parser.add_argument('--epsilon_start', type=float, default=1.0,
                            help='Starting value for epsilon.')
        parser.add_argument('--epsilon_min', type=float, default=0.1,
                            help='Minimum epsilon.')
        parser.add_argument('--epsilon_decay', type=float, default=1000000,
                            help='Number of steps to minimum epsilon.')
        parser.add_argument('--phi_length', type=int, default=4,
                            help='History length')
        parser.add_argument('--max_history', type=int, default=1000000,
                            help='Maximum number of steps stored')
        parser.add_argument('--batch_size', type=int, default=32,
                            help='Batch size')
        parser.add_argument('--exp_pref', type=str, default="",
                            help='Experiment name prefix')
        parser.add_argument('--nn_file', type=str, default=None,
                            help='Pickle file containing trained net.')
        parser.add_argument('--pause', type=float, default=0,
                            help='Amount of time to pause display while testing.')
        parser.add_argument('--nn_trained_share', type=str, default=None, 
                            help='Previously trained file whose weights are going to be shared.')
        parser.add_argument('--share_layers', type=str, default="", 
                            help='1 - first convolutional, 2 - second convolutional, 3 - fully connected. eg. 12 would share layers 1 and 2')
        parser.add_argument('--flip', type=int, default=0, 
                            help='1- flip weights')
        # Create instance variables directy from the arguments:
        parser.parse_known_args(namespace=self)


        # CREATE A FOLDER TO HOLD RESULTS
        time_str = time.strftime("_%m-%d-%H-%M_", time.gmtime())
        self.exp_dir = self.exp_pref + time_str + \
                        "{}".format(self.learning_rate).replace(".", "p") + \
                        "_" + "{}".format(self.discount).replace(".", "p")

        try:
            os.stat(self.exp_dir)
        except:
            os.makedirs(self.exp_dir)



    def agent_init(self, task_spec_string):
        """
        This function is called once at the beginning of an experiment.

        Arguments: task_spec_string - A string defining the task.  This string
                                      is decoded using
                                      TaskSpecVRLGLUE3.TaskSpecParser
        """
        # DO SOME SANITY CHECKING ON THE TASKSPEC
        TaskSpec = TaskSpecVRLGLUE3.TaskSpecParser(task_spec_string)
        if TaskSpec.valid:

            assert ((len(TaskSpec.getIntObservations()) == 0) !=
                    (len(TaskSpec.getDoubleObservations()) == 0)), \
                "expecting continous or discrete observations.  Not both."
            assert len(TaskSpec.getDoubleActions()) == 0, \
                "expecting no continuous actions"
            assert not TaskSpec.isSpecial(TaskSpec.getIntActions()[0][0]), \
                " expecting min action to be a number not a special value"
            assert not TaskSpec.isSpecial(TaskSpec.getIntActions()[0][1]), \
                " expecting max action to be a number not a special value"
            self.num_actions = TaskSpec.getIntActions()[0][1]+1
        else:
            print "INVALID TASK SPEC"

        self.data_set = ale_data_set.DataSet(width=CROPPED_WIDTH,
                                             height=CROPPED_HEIGHT,
                                             max_steps=self.max_history,
                                             phi_length=self.phi_length)

        # just needs to be big enough to create phi's
        self.test_data_set = ale_data_set.DataSet(width=CROPPED_WIDTH,
                                                  height=CROPPED_HEIGHT,
                                                  max_steps=10,
                                                  phi_length=self.phi_length)
        self.epsilon = self.epsilon_start
        if self.epsilon_decay != 0:
            self.epsilon_rate = .9 / self.epsilon_decay
        else:
            self.epsilon_rate = 0
            

        self.testing = False

        if self.nn_file is None:
            self.network = self._init_network()
        else:
            handle = open(self.nn_file, 'r')
            self.network = cPickle.load(handle)

        # If an trained network has been specified, 
        # use it to initialize weights
        if self.nn_trained_share is None:
            print "No sharing between networks"
        else:
            print "Sharing between networks ", self.nn_trained_share
            print "Layers", self.share_layers
            handle = open(self.nn_trained_share, 'r')
            trained_network = cPickle.load(handle)

            if self.share_layers.find('1') != -1:
                # Sharing weights of the first convolutional layer
                print "Sharing weights for Convolution Layer 1"
                self.network.q_layers[2].W.set_value(trained_network.q_layers[2].W.get_value())
                self.network.q_layers[2].b.set_value(trained_network.q_layers[2].b.get_value())
                self.network.q_layers[2].bias_params[0].set_value(trained_network.q_layers[2].bias_params[0].get_value())

                if self.flip == 1:
                    print "Flipping weights in the first convolutional layer"
                    W_old = trained_network.q_layers[2].W.get_value()
                    for i in xrange(4):
                        for j in xrange(16):
                            temp = W_old[i, :, :, j]
                            W_old[i, :, :, j] = temp[::-1].T
                    self.network.q_layers[2].W.set_value(W_old)

                if self.flip == -1:
                    print "Flipping weights counterclockwise in the first convolutional layer"
                    W_old = trained_network.q_layers[2].W.get_value()
                    for i in xrange(4):
                        for j in xrange(16):
                            temp = W_old[i, :, :, j]
                            temp = temp.T
                            W_old[i, :, :, j] = temp[::-1]
                    self.network.q_layers[2].W.set_value(W_old)
                 
                            


            # Sharing weights of the second convolutional layer
            if self.share_layers.find('2') != -1:
                print "Sharing weights for Convolution Layer 2"
                self.network.q_layers[3].W.set_value(trained_network.q_layers[3].W.get_value())
                self.network.q_layers[3].b.set_value(trained_network.q_layers[3].b.get_value())
                self.network.q_layers[3].bias_params[0].set_value(trained_network.q_layers[3].bias_params[0].get_value())

            # Sharing weights of the fully connected layer
            if self.share_layers.find('3') != -1:
                print "Sharing weights for FC layer"
                self.network.q_layers[5].W.set_value(trained_network.q_layers[5].W.get_value())
                self.network.q_layers[5].b.set_value(trained_network.q_layers[5].b.get_value())
                self.network.q_layers[5].bias_params[0].set_value(trained_network.q_layers[5].bias_params[0].get_value())

        self._open_results_file()
        self._open_learning_file()

        self.step_counter = 0
        self.episode_counter = 0
        self.batch_counter = 0

        self.holdout_data = None

        # In order to add an element to the data set we need the
        # previous state and action and the current reward.  These
        # will be used to store states and actions.
        self.last_img = None
        self.last_action = None



    def _init_network(self):
        """
        A subclass may override this if a different sort
        of network is desired.
        """
        return cnn_q_learner.CNNQLearner(self.num_actions,
                                         self.phi_length,
                                         CROPPED_WIDTH,
                                         CROPPED_HEIGHT,
                                         discount=self.discount,
                                         learning_rate=self.learning_rate,
                                         decay=self.rms_decay,
                                         momentum=self.momentum,
                                         batch_size=self.batch_size,
                                         approximator='cuda_conv')
        


    def _open_results_file(self):
        print "OPENING ", self.exp_dir + '/results.csv'
        self.results_file = open(self.exp_dir + '/results.csv', 'w', 0)
        self.results_file.write(\
            'epoch,num_episodes,total_reward,reward_per_epoch,mean_q\n')

    def _open_learning_file(self):
        self.learning_file = open(self.exp_dir + '/learning.csv', 'w', 0)
        self.learning_file.write('mean_loss,epsilon\n')

    def _update_results_file(self, epoch, num_episodes, holdout_sum):
        out = "{},{},{},{},{}\n".format(epoch, num_episodes, self.total_reward,
                                  self.total_reward / float(num_episodes),
                                  holdout_sum)
        self.results_file.write(out)


    def _update_learning_file(self):
        out = "{},{}\n".format(np.mean(self.loss_averages),
                               self.epsilon)
        self.learning_file.write(out)


    def agent_start(self, observation):
        """
        This method is called once at the beginning of each episode.
        No reward is provided, because reward is only available after
        an action has been taken.

        Arguments:
           observation - An observation of type rlglue.types.Observation

        Returns:
           An action of type rlglue.types.Action
        """

        self.step_counter = 0
        self.batch_counter = 0

        # We report the mean loss for every epoch.
        self.loss_averages = []

        self.start_time = time.time()
        this_int_action = self.randGenerator.randint(0, self.num_actions-1)
        return_action = Action()
        return_action.intArray = [this_int_action]

        self.last_action = copy.deepcopy(return_action)

        self.last_img = self._resize_observation(observation.intArray)

        return return_action


    def _show_phis(self, phi1, phi2):
        for p in range(self.phi_length):
            plt.subplot(2, self.phi_length, p+1)
            plt.imshow(phi1[p, :, :], interpolation='none', cmap="gray")
            plt.grid(color='r', linestyle='-', linewidth=1)
        for p in range(self.phi_length):
            plt.subplot(2, self.phi_length, p+5)
            plt.imshow(phi2[p, :, :], interpolation='none', cmap="gray")
            plt.grid(color='r', linestyle='-', linewidth=1)
        plt.show()

    def _resize_observation(self, observation):
        # reshape linear to original image size, skipping the RAM bit
        image = observation[128:].reshape(IMAGE_HEIGHT, IMAGE_WIDTH, 3)
        # convert from int32s
        image = np.array(image, dtype="uint8")

        # convert to greyscale
        greyscaled = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

        # resize keeping aspect ratio
        resize_width = CROPPED_WIDTH
        resize_height = int(round(float(IMAGE_HEIGHT) * CROPPED_HEIGHT / 
                                  IMAGE_WIDTH))

        resized = cv2.resize(greyscaled, (resize_width, resize_height),
        interpolation=cv2.INTER_LINEAR)

        # Crop the part we want
        crop_y_cutoff = resize_height - CROP_OFFSET - CROPPED_HEIGHT
        cropped = resized[crop_y_cutoff:crop_y_cutoff + CROPPED_HEIGHT, :]

        return cropped


    def agent_step(self, reward, observation):
        """
        This method is called each time step.

        Arguments:
           reward      - Real valued reward.
           observation - An observation of type rlglue.types.Observation

        Returns:
           An action of type rlglue.types.Action

        """

        self.step_counter += 1
        return_action = Action()

        cur_img = self._resize_observation(observation.intArray)

        #TESTING---------------------------
        if self.testing:
            self.total_reward += reward
            int_action = self._choose_action(self.test_data_set, .05,
                                             cur_img, np.clip(reward, -1, 1))
            if self.pause > 0:
                time.sleep(self.pause)

        #NOT TESTING---------------------------
        else:
            self.epsilon = max(self.epsilon_min, 
                               self.epsilon - self.epsilon_rate)

            int_action = self._choose_action(self.data_set, self.epsilon,
                                             cur_img, np.clip(reward, -1, 1))

            if len(self.data_set) > self.batch_size:
                loss = self._do_training()
                self.batch_counter += 1
                self.loss_averages.append(loss)

        return_action.intArray = [int_action]

        self.last_action = copy.deepcopy(return_action)
        self.last_img = cur_img

        return return_action

    def _choose_action(self, data_set, epsilon, cur_img, reward):
        """
        Add the most recent data to the data set and choose
        an action based on the current policy.
        """

        data_set.add_sample(self.last_img,
                            self.last_action.intArray[0],
                            reward, False)
        if self.step_counter >= self.phi_length:
            phi = data_set.phi(cur_img)
            int_action = self.network.choose_action(phi, epsilon)
        else:
            int_action = self.randGenerator.randint(0, self.num_actions - 1)
        return int_action

    def _do_training(self):
        """
        Returns the average loss for the current batch.
        May be overridden if a subclass needs to train the network
        differently.
        """
        states, actions, rewards, next_states, terminals = \
                                self.data_set.random_batch(self.batch_size)
        return self.network.train(states, actions, rewards,
                                  next_states, terminals)


    def agent_end(self, reward):
        """
        This function is called once at the end of an episode.

        Arguments:
           reward      - Real valued reward.

        Returns:
            None
        """
        self.episode_counter += 1
        self.step_counter += 1
        total_time = time.time() - self.start_time

        if self.testing:
            self.total_reward += reward
        else:
            print "Simulated at a rate of {}/s \n Average loss: {}".format(\
                self.batch_counter/total_time,
                np.mean(self.loss_averages))

            self._update_learning_file()

            # Store the latest sample.
            self.data_set.add_sample(self.last_img,
                                     self.last_action.intArray[0],
                                     np.clip(reward, -1, 1),
                                     True)


    def agent_cleanup(self):
        """
        Called once at the end of an experiment.  We could save results
        here, but we use the agent_message mechanism instead so that
        a file name can be provided by the experiment.
        """
        pass

    def agent_message(self, in_message):
        """
        The experiment will cause this method to be called.  Used
        to save data to the indicated file.
        """

        #WE NEED TO DO THIS BECAUSE agent_end is not called
        # we run out of steps.
        if in_message.startswith("episode_end"):
            self.agent_end(0)

        elif in_message.startswith("finish_epoch"):
            epoch = int(in_message.split(" ")[1])
            net_file = open(self.exp_dir + '/network_file_' + str(epoch) + \
                            '.pkl', 'w')
            cPickle.dump(self.network, net_file, -1)
            net_file.close()

        elif in_message.startswith("start_testing"):
            self.testing = True
            self.total_reward = 0
            self.episode_counter = 0

        elif in_message.startswith("finish_testing"):
            self.testing = False
            holdout_size = 3200
            epoch = int(in_message.split(" ")[1])

            if self.holdout_data is None:
                self.holdout_data = self.data_set.random_batch(holdout_size)[0]

            holdout_sum = 0
            for i in range(holdout_size):
                holdout_sum += np.mean(
                    self.network.q_vals(self.holdout_data[i, ...]))

            self._update_results_file(epoch, self.episode_counter,
                                      holdout_sum / holdout_size)
        else:
            return "I don't know how to respond to your message"

def main():
    AgentLoader.loadAgent(NeuralAgent())


if __name__ == "__main__":
    main()
