# Machine Learning Basics

## Supervised vs Unsupervised Learning

Machine learning algorithms fall into two main categories based on how they learn from data. Supervised learning uses labeled training data where each input has a known correct output. The algorithm learns a mapping from inputs to outputs. Examples include email spam detection (input: email text, output: spam or not spam) and house price prediction (input: house features, output: price). Common supervised algorithms include linear regression, logistic regression, decision trees, random forests, support vector machines, and neural networks.

Unsupervised learning works with unlabeled data and discovers hidden patterns or structures. The algorithm finds groups, anomalies, or compressed representations without being told what to look for. Common tasks include clustering (grouping similar items), dimensionality reduction (compressing data while preserving structure), and anomaly detection (finding unusual data points). K-means clustering, DBSCAN, PCA (Principal Component Analysis), and autoencoders are popular unsupervised methods.

There is also semi-supervised learning (a mix of labeled and unlabeled data) and reinforcement learning (an agent learns by interacting with an environment and receiving rewards or penalties). Self-supervised learning, where the model generates its own labels from the data structure, has become dominant in natural language processing with models like BERT and GPT.

## Classification vs Regression

Classification predicts discrete categories. Binary classification has two classes (spam vs not spam, fraudulent vs legitimate). Multi-class classification has more than two classes (classifying images into cat, dog, bird, etc.). The output is a class label, often with an associated probability. Evaluation metrics for classification include accuracy, precision, recall, F1 score, and area under the ROC curve (AUC-ROC).

Regression predicts continuous numerical values. Examples include predicting temperature, stock prices, or the time it takes to deliver a package. The output is a number, and the model learns the relationship between input features and the output value. Evaluation metrics for regression include Mean Absolute Error (MAE), Mean Squared Error (MSE), Root Mean Squared Error (RMSE), and R-squared (coefficient of determination).

## Overfitting and Underfitting

Overfitting occurs when a model learns the training data too well, including noise and random fluctuations. The model performs excellently on training data but poorly on new, unseen data. Signs of overfitting include a large gap between training and validation performance, and very high training accuracy. Overfitting is more likely with complex models, small datasets, and too many features relative to samples.

Underfitting occurs when a model is too simple to capture the underlying patterns in the data. The model performs poorly on both training and test data. Signs of underfitting include low training accuracy and low validation accuracy with minimal gap between them. Underfitting is more likely with overly simple models, insufficient features, or too much regularization.

The goal is to find the sweet spot between overfitting and underfitting, where the model generalizes well to new data. This is sometimes called the bias-variance tradeoff.

## Bias-Variance Tradeoff

Bias is the error from oversimplifying assumptions in the model. High-bias models miss important relationships between features and outputs (underfitting). A linear model trying to fit a quadratic relationship has high bias.

Variance is the error from sensitivity to small fluctuations in the training data. High-variance models capture noise as if it were a real pattern (overfitting). A deep decision tree that changes dramatically with small changes in training data has high variance.

The total prediction error is the sum of bias squared, variance, and irreducible noise. Reducing bias typically increases variance and vice versa. Simple models have high bias and low variance. Complex models have low bias and high variance. The optimal model minimizes the total error by balancing both.

## Cross-Validation

Cross-validation is a technique for estimating how well a model generalizes to unseen data. The most common form is k-fold cross-validation: the dataset is divided into k equal parts (folds). The model is trained k times, each time using k-1 folds for training and 1 fold for validation. The final performance is the average across all k runs.

Five-fold and ten-fold cross-validation are standard choices. Leave-one-out cross-validation (LOOCV) uses k equal to the number of samples, which is computationally expensive but gives an unbiased estimate. Stratified k-fold ensures each fold has the same proportion of classes as the full dataset, which is important for imbalanced data.

Cross-validation provides a more reliable performance estimate than a single train-test split because every data point is used for both training and validation. It also helps detect overfitting: if the model performs well on training folds but poorly on validation folds, it is overfitting.

## Regularization

Regularization prevents overfitting by adding a penalty for model complexity to the loss function. L1 regularization (Lasso) adds the sum of absolute values of coefficients, which can drive some coefficients to exactly zero, performing feature selection. L2 regularization (Ridge) adds the sum of squared coefficients, which shrinks coefficients toward zero but rarely makes them exactly zero. Elastic Net combines both L1 and L2.

The regularization strength is controlled by a hyperparameter (often called lambda or alpha). Higher values mean more regularization and simpler models (higher bias, lower variance). Lower values mean less regularization and more complex models (lower bias, higher variance). The optimal regularization strength is typically found through cross-validation.

Dropout is a regularization technique for neural networks where random neurons are temporarily removed during training. This prevents neurons from co-adapting and forces the network to learn more robust features. Typical dropout rates range from 0.2 to 0.5.

## Gradient Descent

Gradient descent is the primary optimization algorithm for training machine learning models. It iteratively adjusts model parameters to minimize a loss function. The gradient (vector of partial derivatives) points in the direction of steepest increase, so moving in the opposite direction decreases the loss.

Batch gradient descent computes the gradient using the entire dataset, which is accurate but slow for large datasets. Stochastic Gradient Descent (SGD) computes the gradient using a single sample, which is noisy but fast. Mini-batch gradient descent uses a small batch of samples (typically 32 to 256), balancing accuracy and speed. Mini-batch is the standard in practice.

The learning rate controls step size. Too large a learning rate causes divergence (overshooting the minimum). Too small causes extremely slow convergence. Learning rate schedules (reducing the rate over time) and adaptive methods like Adam, RMSprop, and AdaGrad adjust the learning rate automatically during training.

## Feature Engineering

Feature engineering is the process of creating, transforming, and selecting input features to improve model performance. It is often the most impactful step in a machine learning pipeline. Common techniques include one-hot encoding for categorical variables, normalization and standardization for numerical features, creating interaction features (products of two features), polynomial features, and domain-specific transformations like log transforms for skewed distributions.

Feature selection removes irrelevant or redundant features. Methods include filter methods (correlation, mutual information), wrapper methods (forward selection, backward elimination), and embedded methods (L1 regularization, tree-based feature importance). Reducing the number of features helps prevent overfitting, speeds up training, and improves model interpretability.

## Ensemble Methods

Ensemble methods combine multiple models to produce better predictions than any single model. Bagging (Bootstrap Aggregating) trains multiple models on random subsets of the data and averages their predictions. Random Forest is a bagging method that also randomly selects features for each tree, reducing correlation between trees.

Boosting trains models sequentially, with each model focusing on the mistakes of the previous one. AdaBoost, Gradient Boosting, XGBoost, LightGBM, and CatBoost are popular boosting algorithms. Gradient boosting methods are consistently among the top performers on structured data in machine learning competitions.

Stacking trains a meta-model to combine the predictions of multiple base models. The base models make predictions, and the meta-model learns the best way to combine them. Ensembles generally improve accuracy by reducing variance, but they increase training time, memory usage, and model complexity.
